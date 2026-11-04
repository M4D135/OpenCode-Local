// OpenCode Local: bounded, offline model assembly and integrity verification.
// Windows build: x86_64-w64-mingw32-g++ -std=c++17 -O2 -static -municode
// Portable validation build: g++ -std=c++17 -O2

#ifdef _WIN32
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#include "pack_config.h"

namespace fs = std::filesystem;

namespace {

constexpr uint64_t kModelBytes = PackConfig::ModelBytes;
constexpr auto& kModelSha256 = PackConfig::ModelSha256;
constexpr size_t kBufferBytes = 4 * 1024 * 1024;

constexpr bool ValidPartSizes() {
    if (!kModelBytes || !PackConfig::PartCount ||
        PackConfig::PartCount != sizeof(PackConfig::Parts) / sizeof(PackConfig::Parts[0])) return false;
    uint64_t total = 0;
    for (size_t i = 0; i < PackConfig::PartCount; ++i) {
        const uint64_t bytes = PackConfig::Parts[i].bytes;
        if (!bytes || bytes > kModelBytes - total) return false;
        total += bytes;
    }
    return total == kModelBytes;
}
static_assert(ValidPartSizes(), "Pack parts must contain exactly the pinned model size");
static_assert(sizeof(PackConfig::ModelSha256) == 65, "A pinned SHA-256 is required");

// Stable exit codes consumed by Setup.exe:
// 0 success (including reuse after a complete SHA-256 check)
// 2 invalid arguments
// 3 source files missing, non-regular, or wrong sizes
// 4 invalid / inaccessible destination or reparse point
// 5 input read error
// 6 output write / flush error (including insufficient space)
// 7 SHA-256 mismatch
// 8 atomic publication error
// 9 integrity self-test failure
// 10 unexpected error
struct Failure : std::runtime_error {
    int code;
    Failure(int value, const std::string& text) : std::runtime_error(text), code(value) {}
};

class Sha256 {
    std::array<uint32_t, 8> state_{{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19}};
    std::array<uint8_t, 64> block_{};
    uint64_t bytes_ = 0;
    size_t used_ = 0;

    static uint32_t Rotate(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }

    void Transform(const uint8_t* block) {
        static constexpr std::array<uint32_t, 64> k{{
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
        }};
        std::array<uint32_t, 64> words{};
        for (size_t i = 0; i < 16; ++i) {
            words[i] = (uint32_t(block[4*i]) << 24) | (uint32_t(block[4*i+1]) << 16) |
                       (uint32_t(block[4*i+2]) << 8) | uint32_t(block[4*i+3]);
        }
        for (size_t i = 16; i < words.size(); ++i) {
            uint32_t s0 = Rotate(words[i-15], 7) ^ Rotate(words[i-15], 18) ^ (words[i-15] >> 3);
            uint32_t s1 = Rotate(words[i-2], 17) ^ Rotate(words[i-2], 19) ^ (words[i-2] >> 10);
            words[i] = words[i-16] + s0 + words[i-7] + s1;
        }
        uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
        uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
        for (size_t i = 0; i < words.size(); ++i) {
            uint32_t s1 = Rotate(e, 6) ^ Rotate(e, 11) ^ Rotate(e, 25);
            uint32_t choose = (e & f) ^ (~e & g);
            uint32_t t1 = h + s1 + choose + k[i] + words[i];
            uint32_t s0 = Rotate(a, 2) ^ Rotate(a, 13) ^ Rotate(a, 22);
            uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            uint32_t t2 = s0 + majority;
            h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
        state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
    }

public:
    void Update(const void* memory, size_t length) {
        const auto* data = static_cast<const uint8_t*>(memory);
        bytes_ += length;
        while (length) {
            if (used_ == 0 && length >= 64) {
                Transform(data); data += 64; length -= 64;
                continue;
            }
            size_t count = std::min(length, block_.size() - used_);
            std::memcpy(block_.data() + used_, data, count);
            used_ += count; data += count; length -= count;
            if (used_ == block_.size()) { Transform(block_.data()); used_ = 0; }
        }
    }

    std::string Hex() const {
        Sha256 copy = *this;
        uint64_t bits = bytes_ * 8;
        std::array<uint8_t, 128> padding{}; padding[0] = 0x80;
        size_t paddingBytes = used_ < 56 ? 56 - used_ : 120 - used_;
        copy.Update(padding.data(), paddingBytes);
        uint8_t size[8]{};
        for (size_t i = 0; i < 8; ++i) size[7 - i] = static_cast<uint8_t>(bits >> (8*i));
        copy.Update(size, sizeof(size));
        constexpr char hex[] = "0123456789abcdef";
        std::string out; out.reserve(64);
        for (uint32_t word : copy.state_) {
            for (int i = 7; i >= 0; --i) out.push_back(hex[(word >> (i * 4)) & 15]);
        }
        return out;
    }
};

std::string SystemErrorText() {
#ifdef _WIN32
    return "code Windows " + std::to_string(GetLastError());
#else
    return "code systeme " + std::to_string(errno);
#endif
}

void RejectReparseAncestors(const fs::path& input) {
    const fs::path absolute = fs::absolute(input).lexically_normal();
    fs::path current = absolute.root_path();
    for (const auto& component : absolute.relative_path()) {
        current /= component;
#ifdef _WIN32
        DWORD attributes = GetFileAttributesW(current.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
            throw Failure(4, "Le chemin de destination traverse une jonction ou un lien. Choisissez un dossier local normal.");
#else
        std::error_code ec;
        auto status = fs::symlink_status(current, ec);
        if (!ec && fs::is_symlink(status))
            throw Failure(4, "Le chemin de destination traverse un lien symbolique.");
#endif
    }
}

bool SameFileOrPath(const fs::path& first, const fs::path& second) {
    std::error_code ec;
    if (fs::equivalent(first, second, ec) && !ec) return true;
#ifdef _WIN32
    std::wstring a = first.native(), b = second.native();
    std::transform(a.begin(), a.end(), a.begin(), [](wchar_t c) { return std::towlower(c); });
    std::transform(b.begin(), b.end(), b.begin(), [](wchar_t c) { return std::towlower(c); });
    return a == b;
#else
    return first == second;
#endif
}

class Reader {
#ifdef _WIN32
    HANDLE handle_ = INVALID_HANDLE_VALUE;
#else
    int handle_ = -1;
#endif
    uint64_t size_ = 0;
public:
    explicit Reader(const fs::path& path, int errorCode = 3) {
#ifdef _WIN32
        handle_ = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (handle_ == INVALID_HANDLE_VALUE) throw Failure(errorCode, "Impossible d'ouvrir un fichier du modele : " + SystemErrorText());
        FILE_ATTRIBUTE_TAG_INFO tag{};
        if (!GetFileInformationByHandleEx(handle_, FileAttributeTagInfo, &tag, sizeof(tag)) ||
            (tag.FileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))) {
            CloseHandle(handle_); handle_ = INVALID_HANDLE_VALUE;
            throw Failure(errorCode, "Un fichier du modele est un lien ou un dossier inattendu.");
        }
        LARGE_INTEGER size{};
        if (!GetFileSizeEx(handle_, &size) || size.QuadPart < 0) {
            CloseHandle(handle_); handle_ = INVALID_HANDLE_VALUE;
            throw Failure(errorCode, "Impossible de lire la taille d'un fichier du modele.");
        }
        size_ = static_cast<uint64_t>(size.QuadPart);
#else
        handle_ = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (handle_ < 0) throw Failure(errorCode, "Impossible d'ouvrir un fichier du modele : " + SystemErrorText());
        struct stat info{};
        if (fstat(handle_, &info) != 0 || !S_ISREG(info.st_mode) || info.st_size < 0) {
            close(handle_); handle_ = -1;
            throw Failure(errorCode, "Le fichier du modele n'est pas un fichier normal.");
        }
        size_ = static_cast<uint64_t>(info.st_size);
#endif
    }
    ~Reader() {
#ifdef _WIN32
        if (handle_ != INVALID_HANDLE_VALUE) CloseHandle(handle_);
#else
        if (handle_ >= 0) close(handle_);
#endif
    }
    Reader(const Reader&) = delete;
    Reader& operator=(const Reader&) = delete;
    uint64_t size() const { return size_; }
    size_t Read(void* buffer, size_t capacity) {
#ifdef _WIN32
        DWORD bytes = 0;
        if (!ReadFile(handle_, buffer, static_cast<DWORD>(capacity), &bytes, nullptr))
            throw Failure(5, "Lecture du modele interrompue : " + SystemErrorText());
        return bytes;
#else
        ssize_t bytes;
        do { bytes = read(handle_, buffer, capacity); } while (bytes < 0 && errno == EINTR);
        if (bytes < 0) throw Failure(5, "Lecture du modele interrompue : " + SystemErrorText());
        return static_cast<size_t>(bytes);
#endif
    }
};

class TemporaryOutput {
    fs::path path_;
#ifdef _WIN32
    HANDLE handle_ = INVALID_HANDLE_VALUE;
#else
    int handle_ = -1;
#endif
    bool owned_ = false;

    void Close() {
#ifdef _WIN32
        if (handle_ != INVALID_HANDLE_VALUE) { CloseHandle(handle_); handle_ = INVALID_HANDLE_VALUE; }
#else
        if (handle_ >= 0) { close(handle_); handle_ = -1; }
#endif
    }
public:
    explicit TemporaryOutput(const fs::path& destination) {
#ifdef _WIN32
        auto processId = GetCurrentProcessId();
#else
        auto processId = getpid();
#endif
        for (unsigned attempt = 0; attempt < 100; ++attempt) {
            path_ = destination;
            path_ += ".partial." + std::to_string(processId) + "." + std::to_string(attempt);
#ifdef _WIN32
            handle_ = CreateFileW(path_.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                                  FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
            if (handle_ != INVALID_HANDLE_VALUE) { owned_ = true; return; }
            DWORD error = GetLastError();
            if (error != ERROR_FILE_EXISTS && error != ERROR_ALREADY_EXISTS)
                throw Failure(4, "Impossible de creer le fichier temporaire du modele : code Windows " + std::to_string(error));
#else
            handle_ = open(path_.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
            if (handle_ >= 0) { owned_ = true; return; }
            if (errno != EEXIST) throw Failure(4, "Impossible de creer le fichier temporaire du modele : " + SystemErrorText());
#endif
        }
        throw Failure(4, "Trop de fichiers temporaires existent dans ce dossier. Aucun fichier existant n'a ete supprime.");
    }
    ~TemporaryOutput() {
        Close();
        if (owned_) {
#ifdef _WIN32
            DeleteFileW(path_.c_str());
#else
            unlink(path_.c_str());
#endif
        }
    }
    TemporaryOutput(const TemporaryOutput&) = delete;
    TemporaryOutput& operator=(const TemporaryOutput&) = delete;

    void Write(const void* data, size_t length) {
        const auto* current = static_cast<const uint8_t*>(data);
        while (length) {
#ifdef _WIN32
            DWORD written = 0;
            if (!WriteFile(handle_, current, static_cast<DWORD>(length), &written, nullptr) || !written)
                throw Failure(6, "Ecriture du modele interrompue. Verifiez l'espace disque : " + SystemErrorText());
            size_t count = written;
#else
            ssize_t written;
            do { written = write(handle_, current, length); } while (written < 0 && errno == EINTR);
            if (written <= 0) throw Failure(6, "Ecriture du modele interrompue. Verifiez l'espace disque : " + SystemErrorText());
            size_t count = static_cast<size_t>(written);
#endif
            current += count; length -= count;
        }
    }

    void Publish(const fs::path& destination) {
#ifdef _WIN32
        if (!FlushFileBuffers(handle_)) throw Failure(6, "Impossible de finaliser l'ecriture du modele : " + SystemErrorText());
#else
        if (fsync(handle_) != 0) throw Failure(6, "Impossible de finaliser l'ecriture du modele : " + SystemErrorText());
#endif
        Close();
        RejectReparseAncestors(destination);
#ifdef _WIN32
        if (!MoveFileExW(path_.c_str(), destination.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
            throw Failure(8, "Impossible de publier le modele verifie : " + SystemErrorText());
#else
        if (rename(path_.c_str(), destination.c_str()) != 0)
            throw Failure(8, "Impossible de publier le modele verifie : " + SystemErrorText());
#endif
        owned_ = false;
    }
};

std::string HashFile(const fs::path& path) {
    Reader input(path, 4);
    Sha256 hash; std::vector<uint8_t> buffer(kBufferBytes);
    uint64_t count = 0;
    for (;;) {
        size_t bytes = input.Read(buffer.data(), buffer.size());
        if (!bytes) break;
        hash.Update(buffer.data(), bytes); count += bytes;
    }
    if (count != input.size()) throw Failure(5, "La taille du fichier a change pendant sa verification.");
    return hash.Hex();
}

bool IntegritySelfTest() {
    struct Vector { const char* text; const char* hash; };
    const Vector cases[] = {
        {"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
        {"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
        {"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"}
    };
    for (const auto& item : cases) {
        Sha256 hash;
        for (const char* p = item.text; *p; ++p) hash.Update(p, 1); // Exercise incremental blocks.
        if (hash.Hex() != item.hash) return false;
    }
    std::array<char, 1000> a{}; a.fill('a');
    Sha256 million;
    for (int i = 0; i < 1000; ++i) million.Update(a.data(), a.size());
    return million.Hex() == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0";
}

int Assemble(const fs::path& sourceDirectory, const fs::path& destinationInput) {
    if (!IntegritySelfTest()) throw Failure(9, "Le controle interne SHA-256 a echoue.");
    const fs::path source = fs::absolute(sourceDirectory).lexically_normal();
    const fs::path destination = fs::absolute(destinationInput).lexically_normal();
    if (destination.filename().empty()) throw Failure(4, "Le chemin du modele de destination est invalide.");
    const fs::path completeSource = source / fs::u8path(PackConfig::ModelFile);
    if (SameFileOrPath(completeSource, destination))
        throw Failure(4, "La destination ne peut pas remplacer le fichier GGUF source.");
    for (size_t i = 0; i < PackConfig::PartCount; ++i) {
        if (SameFileOrPath(source / fs::u8path(PackConfig::Parts[i].name), destination))
            throw Failure(4, "La destination ne peut pas remplacer une partie originale du modele.");
    }
    RejectReparseAncestors(destination);
    std::error_code ec;
    const bool destinationExists = fs::exists(destination, ec);
    if (ec) throw Failure(4, "Impossible de verifier le fichier de destination.");
    if (destinationExists) {
        if (!fs::is_regular_file(destination)) throw Failure(4, "La destination n'est pas un fichier normal.");
        if (fs::file_size(destination) == kModelBytes) {
            std::cout << "Verification du modele deja present..." << std::endl;
            if (HashFile(destination) == kModelSha256) {
                std::cout << "Modele existant valide : SHA-256 confirme, aucune copie necessaire." << std::endl;
                return 0;
            }
            std::cout << "Le fichier existant est different. Il sera remplace uniquement apres verification du nouveau modele." << std::endl;
        }
    }
    // A complete, exactly named GGUF is an alternative to all split parts.
    // Prefer it when present, but never hide an invalid full file by silently
    // falling back to the parts. Keep the input handles open for the whole
    // copy; on Windows they do not permit concurrent writes or deletions.
    std::vector<std::unique_ptr<Reader>> inputs;
    auto addInput = [&inputs](const fs::path& path, uint64_t expectedBytes) {
        auto input = std::make_unique<Reader>(path);
        if (input->size() != expectedBytes)
            throw Failure(3, "Taille incorrecte pour " + path.filename().u8string() +
                             ". Le fichier doit contenir " + std::to_string(expectedBytes) + " octets.");
        inputs.push_back(std::move(input));
    };
    ec.clear();
    const auto sourceStatus = fs::symlink_status(completeSource, ec);
    if (ec && ec != std::errc::no_such_file_or_directory && ec != std::errc::not_a_directory)
        throw Failure(3, "Impossible de verifier le fichier GGUF source.");
    const bool completePresent = !ec && sourceStatus.type() != fs::file_type::not_found;
    if (completePresent) {
        addInput(completeSource, kModelBytes);
        std::cout << "Source : fichier GGUF complet " << PackConfig::ModelFile << std::endl;
    } else {
        for (size_t i = 0; i < PackConfig::PartCount; ++i)
            addInput(source / fs::u8path(PackConfig::Parts[i].name), PackConfig::Parts[i].bytes);
        std::cout << "Source : " << PackConfig::PartCount << " parties du modele." << std::endl;
    }
    RejectReparseAncestors(destination.parent_path());
    fs::create_directories(destination.parent_path(), ec);
    if (ec) throw Failure(4, "Impossible de creer le dossier du modele.");
    auto space = fs::space(destination.parent_path(), ec);
    if (!ec && space.available < kModelBytes + 16 * 1024 * 1024ULL)
        throw Failure(6, "Espace disque insuffisant pour preparer le modele. Il faut au moins " +
                         std::to_string((kModelBytes + 1024 * 1024 - 1) / (1024 * 1024) + 16) + " Mio libres.");
    TemporaryOutput output(destination);
    Sha256 hash; std::vector<uint8_t> buffer(kBufferBytes);
    uint64_t total = 0;
    std::array<uint8_t, 4> magic{};
    size_t magicBytes = 0;
    int lastPercent = -10;
    std::cout << "Assemblage du modele hors ligne et verification SHA-256..." << std::endl;
    for (const auto& input : inputs) {
        uint64_t readBytes = 0;
        while (readBytes < input->size()) {
            size_t wanted = static_cast<size_t>(std::min<uint64_t>(buffer.size(), input->size() - readBytes));
            size_t received = input->Read(buffer.data(), wanted);
            if (!received) throw Failure(5, "Une partie du modele s'est terminee avant la taille attendue.");
            if (magicBytes < magic.size()) {
                const size_t count = std::min(magic.size() - magicBytes, received);
                std::copy_n(buffer.data(), count, magic.data() + magicBytes);
                magicBytes += count;
                if (magicBytes == magic.size() && std::memcmp(magic.data(), "GGUF", magic.size()) != 0)
                    throw Failure(7, "La source n'est pas un fichier GGUF brut. Les archives et fichiers de quarantaine ne sont pas acceptes.");
            }
            hash.Update(buffer.data(), received);
            output.Write(buffer.data(), received);
            readBytes += received; total += received;
            int percent = static_cast<int>((total * 100) / kModelBytes);
            if (percent >= lastPercent + 10 || percent == 100) {
                std::cout << "Modele : " << percent << " %" << std::endl;
                lastPercent = percent;
            }
        }
        uint8_t extra = 0;
        if (input->Read(&extra, 1) != 0) throw Failure(5, "Une partie du modele a change de taille pendant la copie.");
    }
    if (magicBytes != magic.size() || total != kModelBytes || hash.Hex() != kModelSha256)
        throw Failure(7, "Le SHA-256 du modele ne correspond pas. Le fichier temporaire a ete abandonne ; la destination existante est conservee.");
    output.Publish(destination);
    std::cout << "Modele installe : " << kModelBytes << " octets, SHA-256 confirme." << std::endl;
    return 0;
}

int Run(const std::vector<fs::path>& arguments) {
    try {
        if (arguments.size() == 2 && arguments[1] == fs::path("--self-test")) {
            if (!IntegritySelfTest()) throw Failure(9, "ECHEC SHA-256.");
            std::cout << "PASS SHA-256 : 4 vecteurs, blocs incrementaux et un million d'octets." << std::endl;
            return 0;
        }
        if (arguments.size() == 3 && arguments[1] == fs::path("--hash-file")) {
            std::cout << HashFile(arguments[2]) << std::endl;
            return 0;
        }
        if (arguments.size() != 3) {
            std::cerr << "Usage : AssembleModel.exe DOSSIER_SOURCE CHEMIN_DU_MODELE\n"
                         "Le dossier source doit contenir " << PackConfig::ModelFile <<
                         " ou les " << PackConfig::PartCount << " parties du modele de ce pack.\n";
            return 2;
        }
        return Assemble(arguments[1], arguments[2]);
    } catch (const Failure& failure) {
        std::cerr << "ERREUR " << failure.code << " : " << failure.what() << std::endl;
        return failure.code;
    } catch (const std::exception& failure) {
        std::cerr << "ERREUR 10 : " << failure.what() << std::endl;
        return 10;
    }
}

} // namespace

#ifdef _WIN32
int wmain(int count, wchar_t** values) {
    std::vector<fs::path> arguments;
    for (int i = 0; i < count; ++i) arguments.emplace_back(values[i]);
    return Run(arguments);
}
#else
int main(int count, char** values) {
    std::vector<fs::path> arguments;
    for (int i = 0; i < count; ++i) arguments.emplace_back(values[i]);
    return Run(arguments);
}
#endif

