; Offline installer. Built with native Linux/Windows NSIS 3.09+, without Wine.
; No downloads, system PATH modifications, elevation or firewall changes.
Unicode true
RequestExecutionLevel user
ManifestDPIAware true
ManifestSupportedOS all
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

!ifndef PayloadDir
  !define PayloadDir "../build/payload"
!endif
!ifndef HelperPath
  !define HelperPath "../build/AssembleModel.exe"
!endif
!ifndef OutputPath
  !define OutputPath "../dist/Setup.exe"
!endif
!ifndef PackConfigPath
  !define PackConfigPath "${PayloadDir}/../generated/pack-profile.nsh"
!endif
!include "${PackConfigPath}"
!ifndef AppVersion
  !define AppVersion "${PACK_VERSION}"
!endif
!define PRODUCT_NAME "${PACK_DISPLAY_NAME}"
!define PRODUCT_MARKER "${PACK_MARKER}"
!define PROFILE_DIRECTORY "${PACK_PROFILE_NAME}"
!define SHORTCUT_FOLDER "${PACK_SHORTCUT_NAME}"
!define MARKER_PATH "wad\support\installation.id"
!define MODEL_PATH "wad\model\${PACK_MODEL_FILENAME}"
!define UNINSTALL_KEY "${PACK_UNINSTALL_KEY}"
!include "${PayloadDir}/../payload-files.nsh"

Name "${PRODUCT_NAME}"
Caption "Installation de ${PRODUCT_NAME}"
OutFile "${OutputPath}"
InstallDir "$LOCALAPPDATA\Programs\${PACK_INSTALL_DIR}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
BrandingText "${PACK_DISPLAY_NAME} | Installation hors ligne"
VIProductVersion "${AppVersion}.0"
; One signing product identity for both RAM profiles and all house binaries.
VIAddVersionKey /LANG=1036 "ProductName" "OpenCode Local"
VIAddVersionKey /LANG=1036 "ProductVersion" "${AppVersion}"
!ifdef ExportUninstallerPath
VIAddVersionKey /LANG=1036 "OriginalFilename" "Uninstall.exe"
VIAddVersionKey /LANG=1036 "FileDescription" "Désinstallation de OpenCode Local"
!else
VIAddVersionKey /LANG=1036 "OriginalFilename" "Setup.exe"
VIAddVersionKey /LANG=1036 "FileDescription" "Installation hors ligne de OpenCode Local"
!endif
VIAddVersionKey /LANG=1036 "FileVersion" "${AppVersion}.0"
VIAddVersionKey /LANG=1036 "CompanyName" "M4D135"
VIAddVersionKey /LANG=1036 "LegalCopyright" "OpenCode Local - M4D135"
!ifdef ExportUninstallerPath
  !ifdef ImportedUninstallerPath
    !error "Cannot export and import an uninstaller in the same compilation"
  !endif
  ; NSIS 3.08+ writes the complete PE at compile time. This hook copies it;
  ; no Windows executable is run on the build host. The outer workflow signs
  ; the exported PE, then the final compilation imports those exact bytes.
  ; https://nsis.sourceforge.io/Signing_an_Uninstaller_externally
  !uninstfinalize '"${PythonExe}" "${SigningHelperPath}" capture-uninstaller "%1" "${ExportUninstallerPath}"' = 0
!endif
; The model already lives in external slices. Zlib keeps builds and install
; extraction fast for the executable/dependency payload on modest CPUs.
SetCompressor zlib
SetDatablockOptimize on
CRCCheck force
ShowInstDetails show
ShowUninstDetails show
AutoCloseWindow false

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Bienvenue dans ${PACK_DISPLAY_NAME}"
!define MUI_WELCOMEPAGE_TEXT "Votre assistant de code, directement sur votre PC.$\r$\n$\r$\nPack pour ${PACK_RAM_GIB} Go de RAM : ${PACK_MODEL_LABEL}. Aucun téléchargement n’est lancé par l’installation.$\r$\n$\r$\nPlacez Setup.exe et le fichier GGUF du modèle (ou toutes ses parties .bin) dans le même dossier.$\r$\n$\r$\nL’installation se fait dans votre session Windows. Vos projets, conversations et réglages restent séparés."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE DirectoryLeave
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "OpenCode Local est installé"
!define MUI_FINISHPAGE_TEXT "Choisissez votre dossier de projet dans le lanceur, puis démarrez OpenCode.$\r$\n$\r$\nLe raccourci est disponible dans le menu Démarrer. Un mode de discussion directe est également inclus.$\r$\n$\r$\nConservez le setup et les fichiers du modèle ensemble pour réinstaller hors ligne."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApplication
!define MUI_FINISHPAGE_RUN_TEXT "Ouvrir OpenCode Local"
!insertmacro MUI_PAGE_FINISH
!ifndef ImportedUninstallerPath
!define MUI_UNCONFIRMPAGE_TEXT_TOP "OpenCode Local et ses composants seront retirés de ce dossier.$\r$\n$\r$\nVos projets, conversations et réglages personnels seront conservés."
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!endif
!insertmacro MUI_LANGUAGE "French"

Var Problem
Var ProfileLock
Var StageDir
Var StageCreated
Var BackupDir
Var Owned
Var HasContent

!macro SharedFunctions PREFIX
Function ${PREFIX}ReleaseProfileLock
  Push $0
  StrCmp $ProfileLock "" release_done
  StrCmp $ProfileLock "0" release_done
  StrCmp $ProfileLock "-1" release_done
  StrCmp $ProfileLock "4294967295" release_done
  System::Call 'kernel32::CloseHandle(p $ProfileLock) i .r0'
release_done:
  StrCpy $ProfileLock ""
  Pop $0
FunctionEnd

; Stack: path. Reject junction/symlink ancestors without following them.
Function ${PREFIX}CheckAncestors
  Exch $0
  Push $1
  Push $2
ancestor_loop:
  StrCmp $0 "" ancestor_done
  System::Call 'kernel32::GetFileAttributesW(w r0) i .r1'
  ${If} $1 != -1
    IntOp $2 $1 & 0x400
    ${If} $2 != 0
      StrCpy $Problem "Le chemin traverse une jonction ou un lien symbolique : $0"
      Goto ancestor_done
    ${EndIf}
  ${EndIf}
  ${GetParent} "$0" $1
  StrCmp $1 $0 ancestor_done
  StrCpy $0 $1
  Goto ancestor_loop
ancestor_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Stack: directory. Inspect attributes recursively, never follow a link.
Function ${PREFIX}CheckTree
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  ClearErrors
  FindFirst $1 $2 "$0\*"
  IfErrors tree_done
tree_loop:
  StrCmp $2 "" tree_close
  StrCmp $2 "." tree_next
  StrCmp $2 ".." tree_next
  StrCpy $4 "$0\$2"
  System::Call 'kernel32::GetFileAttributesW(w r4) i .r3'
  ${If} $3 == -1
    StrCpy $Problem "Un fichier de ce dossier est inaccessible : $4"
    Goto tree_close
  ${EndIf}
  IntOp $3 $3 & 0x400
  ${If} $3 != 0
    StrCpy $Problem "Ce dossier contient une jonction ou un lien symbolique : $4"
    Goto tree_close
  ${EndIf}
  System::Call 'kernel32::GetFileAttributesW(w r4) i .r3'
  IntOp $3 $3 & 0x10
  ${If} $3 != 0
    Push $4
    Call ${PREFIX}CheckTree
    StrCmp $Problem "" tree_next tree_close
  ${EndIf}
tree_next:
  ClearErrors
  FindNext $1 $2
  IfErrors tree_close
  Goto tree_loop
tree_close:
  FindClose $1
tree_done:
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Stack: directory. Return ownership through $Owned.
Function ${PREFIX}ReadOwner
  Exch $0
  Push $1
  Push $2
  StrCpy $Owned 0
  ClearErrors
  FileOpen $1 "$0\${MARKER_PATH}" r
  IfErrors owner_done
  FileRead $1 $2 256
  FileClose $1
  StrCmp $2 "${PRODUCT_MARKER}" owner_yes
  StrCmp $2 "${PRODUCT_MARKER}$\r$\n" owner_yes
  StrCmp $2 "${PRODUCT_MARKER}$\n" owner_yes owner_done
owner_yes:
  StrCpy $Owned 1
owner_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function ${PREFIX}AcquireProfileLock
  Push $0
  StrCpy $Problem ""
  Push "$LOCALAPPDATA\${PROFILE_DIRECTORY}"
  Call ${PREFIX}CheckAncestors
  StrCmp $Problem "" 0 acquire_done
  ClearErrors
  CreateDirectory "$LOCALAPPDATA\${PROFILE_DIRECTORY}"
  IfErrors acquire_failed
  System::Call 'kernel32::CreateFileW(w "$LOCALAPPDATA\${PROFILE_DIRECTORY}\launcher.lock", i 0xC0000000, i 0, p 0, i 4, i 0x80, p 0) p .r0'
  StrCpy $ProfileLock $0
  StrCmp $0 "0" acquire_failed
  StrCmp $0 "-1" acquire_failed
  StrCmp $0 "4294967295" acquire_failed acquire_done
acquire_failed:
  StrCpy $Problem "Fermez OpenCode Local et ses sessions, puis réessayez. Si aucune session n’est ouverte, vérifiez les droits d’écriture de votre dossier de données."
acquire_done:
  Pop $0
FunctionEnd
!macroend
!insertmacro SharedFunctions ""
!ifndef ImportedUninstallerPath
!insertmacro SharedFunctions "un."
!endif

Function .onInit
  SetShellVarContext current
  SetRegView 64
  StrCpy $ProfileLock ""
  StrCpy $StageCreated 0
  StrCpy $StageDir ""
  StrCpy $BackupDir ""
  ${IfNot} ${IsNativeAMD64}
    MessageBox MB_ICONSTOP|MB_OK "Ce pack est prévu pour Windows 64 bits avec un processeur Intel ou AMD." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP|MB_OK "Windows 10 ou Windows 11 est nécessaire." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
FunctionEnd

Function ValidateTarget
  Push $0
  Push $1
  Push $2
  StrCpy $Problem ""
  StrCpy $HasContent 0
  StrCmp $INSTDIR "" target_bad
  ; NSIS GetFullPathName clears its output when the final folder does not
  ; exist. A new installation folder is expected not to exist yet. Use the
  ; Win32 path normalizer, with distinct input/output buffers, instead.
  StrCpy $2 $INSTDIR
  System::Call 'kernel32::GetFullPathNameW(w r2, i ${NSIS_MAX_STRLEN}, w .r1, p 0) i .r0'
  ${If} $0 == 0
    Goto target_bad
  ${EndIf}
  ${If} $0 >= ${NSIS_MAX_STRLEN}
    StrCpy $Problem "Le chemin d’installation est trop long. Choisissez un dossier plus court."
    Goto target_done
  ${EndIf}
  StrCpy $INSTDIR $1
target_trim_separator:
  StrLen $0 $INSTDIR
  ${If} $0 <= 3
    Goto target_bad
  ${EndIf}
  StrCpy $0 $INSTDIR 1 -1
  StrCmp $0 "\" 0 target_normalized
  StrCpy $INSTDIR $INSTDIR -1
  Goto target_trim_separator
target_normalized:
  StrLen $0 $INSTDIR
  ${If} $0 < 4
    Goto target_bad
  ${EndIf}
  StrCpy $0 $INSTDIR 2
  StrCmp $0 "\\" target_bad
  StrCmp $INSTDIR "$LOCALAPPDATA" target_bad
  StrCmp $INSTDIR "$APPDATA" target_bad
  StrCmp $INSTDIR "$PROFILE" target_bad
  StrCmp $INSTDIR "$WINDIR" target_bad
  StrCmp $INSTDIR "$SYSDIR" target_bad
  StrCmp $INSTDIR "$PROGRAMFILES" target_bad
  StrCmp $INSTDIR "$PROGRAMFILES64" target_bad
  StrCmp $INSTDIR "$DESKTOP" target_bad
  StrCmp $INSTDIR "$DOCUMENTS" target_bad
  StrCmp $INSTDIR "$TEMP" target_bad
  StrCpy $0 $INSTDIR
target_profile_loop:
  StrCmp $0 "$LOCALAPPDATA\${PROFILE_DIRECTORY}" target_bad
  ; Neither pack may replace a directory inside the other pack's user data.
  StrCmp $0 "$LOCALAPPDATA\OpenCodeLocal" target_bad
  StrCmp $0 "$LOCALAPPDATA\OpenCodeLocal32Go" target_bad
  ${GetParent} "$0" $1
  StrCmp $1 $0 target_ancestors
  StrCmp $1 "" target_ancestors
  StrCpy $0 $1
  Goto target_profile_loop
target_ancestors:
  Push $INSTDIR
  Call CheckAncestors
  StrCmp $Problem "" 0 target_done
  System::Call 'kernel32::GetFileAttributesW(w "$INSTDIR") i .r0'
  StrCmp $0 -1 target_done
  IntOp $0 $0 & 0x10
  StrCmp $0 0 target_bad
  ClearErrors
  FindFirst $1 $2 "$INSTDIR\*"
  IfErrors target_done
target_content_loop:
  StrCmp $2 "" target_content_close
  StrCmp $2 "." target_content_next
  StrCmp $2 ".." target_content_next
  StrCpy $HasContent 1
  Goto target_content_close
target_content_next:
  FindNext $1 $2
  IfErrors target_content_close
  Goto target_content_loop
target_content_close:
  FindClose $1
  StrCmp $HasContent 0 target_done
  Push $INSTDIR
  Call ReadOwner
  StrCmp $Owned 1 target_tree
  StrCpy $Problem "Le dossier contient déjà des fichiers et ne correspond pas à une installation reconnue de OpenCode Local. Choisissez un dossier vide ou le dossier proposé."
  Goto target_done
target_tree:
  Push $INSTDIR
  Call CheckTree
  Goto target_done
target_bad:
  StrCpy $Problem "Choisissez un dossier local dédié au programme, par exemple $LOCALAPPDATA\Programs\${PACK_INSTALL_DIR}."
target_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function DirectoryLeave
  Call ValidateTarget
  StrCmp $Problem "" directory_valid
  MessageBox MB_ICONSTOP|MB_OK "$Problem" /SD IDOK
  Abort
directory_valid:
FunctionEnd

Function ValidateDataParts
  Push $0
  Push $1
  Push $2
  StrCpy $Problem ""
  ; A complete GGUF can exceed 4 GiB, so use the 64-bit Win32 size API.
  ; If it is present but invalid, do not silently choose another model source.
  IfFileExists "$EXEDIR\${PACK_MODEL_FILENAME}" whole_model parts_split
whole_model:
  System::Call 'kernel32::CreateFileW(w "$EXEDIR\${PACK_MODEL_FILENAME}", i 0x80000000, i 1, p 0, i 3, i 0x80, p 0) p .r0'
  StrCmp $0 "0" parts_bad
  StrCmp $0 "-1" parts_bad
  StrCmp $0 "4294967295" parts_bad
  System::Call 'kernel32::GetFileSizeEx(p r0, *l .r1) i .r2'
  System::Call 'kernel32::CloseHandle(p r0)'
  StrCmp $2 "0" parts_bad
  System::Int64Op $1 = ${PACK_MODEL_BYTES}
  Pop $2
  StrCmp $2 "1" parts_done parts_bad
parts_split:
  !insertmacro ValidatePackParts
parts_missing:
  StrCpy $Problem "Le modèle de ce pack est incomplet. Placez ${PACK_MODEL_FILENAME}, ou ses ${PACK_PART_COUNT} fichiers .bin, dans le même dossier que Setup.exe."
  Goto parts_done
parts_bad:
  StrCpy $Problem "Un fichier du modèle est incomplet ou appartient à un autre pack. Utilisez les fichiers du pack ${PACK_RAM_GIB} Go."
parts_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function CheckDiskSpace
  Push $0
  Push $1
  Push $2
  StrCpy $Problem ""
  ${GetRoot} "$INSTDIR" $0
  StrCpy $0 "$0\"
  System::Call 'kernel32::GetDiskFreeSpaceExW(w r0, *l .r1, p 0, p 0) i .r2'
  StrCmp $2 0 disk_unknown
  System::Int64Op $1 < ${PACK_DISK_REQUIRED_BYTES}
  Pop $2
  StrCmp $2 0 disk_done
  StrCpy $Problem "L’installation demande au moins ${PACK_DISK_REQUIRED_GIB} Go libres sur le disque de destination, le temps de préparer et vérifier le pack."
  Goto disk_done
disk_unknown:
  StrCpy $Problem "Impossible de vérifier l’espace libre sur le disque de destination. Choisissez un dossier local accessible."
disk_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Cleanup uses only explicit package file names; no recursive RMDir anywhere.
Function CleanupStage
  StrCmp $StageCreated 1 0 cleanup_done
  StrCmp $StageDir "" cleanup_done
  Push $StageDir
  Call ReadOwner
  StrCmp $Owned 1 0 cleanup_done
  StrCpy $Problem ""
  Push $StageDir
  Call CheckAncestors
  StrCmp $Problem "" 0 cleanup_done
  Push $StageDir
  Call CheckTree
  StrCmp $Problem "" 0 cleanup_done
  !insertmacro RemovePayload "$StageDir"
cleanup_done:
FunctionEnd

Function InstallationFailure
  Push $0
  StrCpy $0 $Problem
  DetailPrint "Installation interrompue : $Problem"
  Call CleanupStage
  Call ReleaseProfileLock
  StrCpy $Problem $0
  Pop $0
  MessageBox MB_ICONSTOP|MB_OK "$Problem" /SD IDOK
  SetErrorLevel 1
  Abort
FunctionEnd

Function .onInstSuccess
  Call ReleaseProfileLock
FunctionEnd
Function .onInstFailed
  Call CleanupStage
  Call ReleaseProfileLock
FunctionEnd
Function .onGUIEnd
  Call ReleaseProfileLock
FunctionEnd

Section "${PACK_DISPLAY_NAME} et modèle Qwen (obligatoire)" SEC_MAIN
  SectionIn RO
!ifdef ExportUninstallerPath
  ; The first pass only needs the real uninstall code and exact removal list.
  ; Do not embed another copy of the upstream runtime just to export this PE.
  WriteUninstaller "$TEMP\OpenCodeLocal-uninstaller-export.exe"
!else
  AddSize ${PACK_MODEL_EXTRA_KIB}
  Call ValidateTarget
  StrCmp $Problem "" +2
  Call InstallationFailure
  Call ValidateDataParts
  StrCmp $Problem "" +2
  Call InstallationFailure
  Call CheckDiskSpace
  StrCmp $Problem "" +2
  Call InstallationFailure
  Call AcquireProfileLock
  StrCmp $Problem "" +2
  Call InstallationFailure
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=AssembleModel.exe "${HelperPath}"
  System::Call 'kernel32::GetCurrentProcessId() i .r0'
  System::Call 'kernel32::GetTickCount() i .r1'
  StrCpy $StageDir "$INSTDIR.install-$0-$1"
  StrCpy $BackupDir "$INSTDIR.previous-$0-$1"
  System::Call 'kernel32::GetFileAttributesW(w "$StageDir") i .r2'
  ${If} $2 != -1
    StrCpy $Problem "Le dossier temporaire d’installation existe déjà. Relancez l’installation."
    Call InstallationFailure
  ${EndIf}
  ClearErrors
  CreateDirectory "$StageDir\wad\support"
  IfErrors stage_error
  SetOutPath "$StageDir\wad\support"
  File /oname=installation.id "${PayloadDir}/wad/support/installation.id"
  StrCpy $StageCreated 1
  DetailPrint "Installation des composants inclus dans le pack..."
  ClearErrors
  !insertmacro InstallPayload "$StageDir"
  IfErrors extraction_error
  CreateDirectory "$StageDir\wad\model"
  DetailPrint "Préparation et vérification du modèle ${PACK_MODEL_LABEL} (${PACK_MODEL_SIZE_TEXT})..."
  nsExec::ExecToLog /TIMEOUT=3600000 '"$PLUGINSDIR\AssembleModel.exe" "$EXEDIR" "$StageDir\${MODEL_PATH}"'
  Pop $0
  StrCmp $0 "0" model_ready
  StrCpy $Problem "Le modèle n’a pas pu être assemblé ou sa vérification a échoué. Consultez les détails ci-dessus. L’ancienne installation n’a pas été remplacée."
  Call InstallationFailure
model_ready:
  ClearErrors
!ifdef ImportedUninstallerPath
  SetOutPath "$StageDir\wad\support"
  File /oname=Uninstall.exe "${ImportedUninstallerPath}"
!else
  WriteUninstaller "$StageDir\wad\support\Uninstall.exe"
!endif
  IfErrors uninstaller_error
  Call ValidateTarget
  StrCmp $Problem "" +2
  Call InstallationFailure
  System::Call 'kernel32::GetFileAttributesW(w "$INSTDIR") i .r0'
  StrCmp $0 -1 commit_new
  StrCmp $HasContent 0 commit_empty
  System::Call 'kernel32::GetFileAttributesW(w "$BackupDir") i .r0'
  ${If} $0 != -1
    StrCpy $Problem "Le dossier de sauvegarde temporaire existe déjà. Relancez l’installation."
    Call InstallationFailure
  ${EndIf}
  ClearErrors
  Rename "$INSTDIR" "$BackupDir"
  IfErrors backup_error
  Goto commit_new
commit_empty:
  ClearErrors
  RMDir "$INSTDIR"
  IfErrors backup_error
  StrCpy $BackupDir ""
commit_new:
  SetOutPath "$TEMP"
  ClearErrors
  Rename "$StageDir" "$INSTDIR"
  IfErrors commit_restore
  StrCpy $StageCreated 0
  StrCpy $StageDir ""
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${AppVersion}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "M4D135"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\OpenCodeLocal.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\wad\support\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\wad\support\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  CreateDirectory "$SMPROGRAMS\${SHORTCUT_FOLDER}"
  SetOutPath "$INSTDIR"
  CreateShortCut "$SMPROGRAMS\${SHORTCUT_FOLDER}\${SHORTCUT_FOLDER}.lnk" "$INSTDIR\OpenCodeLocal.exe" "" "$INSTDIR\OpenCodeLocal.exe"
  CreateShortCut "$SMPROGRAMS\${SHORTCUT_FOLDER}\Désinstaller.lnk" "$INSTDIR\wad\support\Uninstall.exe"
  StrCmp $BackupDir "" install_complete
  Push $BackupDir
  Call ReadOwner
  StrCmp $Owned 1 0 install_complete
  StrCpy $Problem ""
  Push $BackupDir
  Call CheckAncestors
  StrCmp $Problem "" 0 install_complete
  Push $BackupDir
  Call CheckTree
  StrCmp $Problem "" 0 install_complete
  !insertmacro RemovePayload "$BackupDir"
  IfFileExists "$BackupDir\*" 0 install_complete
  DetailPrint "Les fichiers ajoutés à l’ancienne installation sont conservés dans : $BackupDir"
install_complete:
  DetailPrint "OpenCode Local est installé. Vos données personnelles ont été conservées."
  Goto main_end
stage_error:
  StrCpy $Problem "Impossible de créer le dossier de préparation. Vérifiez les droits d’écriture sur la destination."
  Call InstallationFailure
extraction_error:
  StrCpy $Problem "L’extraction des composants a échoué. Vérifiez l’espace disque et l’intégrité de Setup.exe."
  Call InstallationFailure
uninstaller_error:
  StrCpy $Problem "Impossible de préparer le désinstalleur. L’installation n’a pas été remplacée."
  Call InstallationFailure
backup_error:
  StrCpy $Problem "Le dossier d’installation est utilisé par un programme ou n’est pas modifiable. Fermez les sessions et réessayez."
  Call InstallationFailure
commit_restore:
  StrCmp $BackupDir "" commit_failed
  IfFileExists "$BackupDir\*" 0 commit_failed
  ClearErrors
  Rename "$BackupDir" "$INSTDIR"
  IfErrors rollback_failed
commit_failed:
  StrCpy $Problem "Impossible de mettre en place les fichiers préparés. L’ancienne installation a été conservée."
  Call InstallationFailure
rollback_failed:
  StrCpy $Problem "La mise en place a échoué. L’ancienne installation est conservée dans $BackupDir. Aucun de ses fichiers n’a été supprimé."
  Call InstallationFailure
main_end:
!endif
SectionEnd

Section /o "Raccourci sur le Bureau" SEC_DESKTOP
  SetOutPath "$INSTDIR"
  CreateShortCut "$DESKTOP\${SHORTCUT_FOLDER}.lnk" "$INSTDIR\OpenCodeLocal.exe" "" "$INSTDIR\OpenCodeLocal.exe"
SectionEnd
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN} "Le lanceur, OpenCode, le moteur local, le modèle Qwen et les dépendances hors ligne."
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_DESKTOP} "Ajoute un raccourci OpenCode Local sur votre Bureau."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function LaunchApplication
  Call ReleaseProfileLock
  Exec '"$INSTDIR\OpenCodeLocal.exe"'
FunctionEnd

!ifndef ImportedUninstallerPath
Function un.onInit
  SetShellVarContext current
  SetRegView 64
  StrCpy $ProfileLock ""
  ; NSIS initializes $INSTDIR to the original uninstaller directory, even
  ; when it runs a temporary copy. This uninstaller lives in wad\support.
  ; See https://nsis.sourceforge.io/Docs/Chapter4.html#varother
  GetFullPathName $INSTDIR "$INSTDIR\..\.."
  StrCpy $Problem ""
  Push $INSTDIR
  Call un.CheckAncestors
  StrCmp $Problem "" 0 un_refused
  Push $INSTDIR
  Call un.ReadOwner
  StrCmp $Owned 1 un_owned
  StrCpy $Problem "Le marqueur de cette installation est absent ou incorrect. La désinstallation est interrompue pour préserver les autres fichiers."
  Goto un_refused
un_owned:
  Push $INSTDIR
  Call un.CheckTree
  StrCmp $Problem "" 0 un_refused
  Call un.AcquireProfileLock
  StrCmp $Problem "" un_ready
un_refused:
  MessageBox MB_ICONSTOP|MB_OK "$Problem" /SD IDOK
  SetErrorLevel 1
  Abort
un_ready:
FunctionEnd
Function un.onGUIEnd
  Call un.ReleaseProfileLock
FunctionEnd
Function un.onUninstFailed
  Call un.ReleaseProfileLock
FunctionEnd
Function un.onUninstSuccess
  Call un.ReleaseProfileLock
FunctionEnd

Section "Uninstall"
  SetOutPath "$TEMP"
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  StrCmp $0 $INSTDIR 0 un_files
  Delete "$DESKTOP\${SHORTCUT_FOLDER}.lnk"
  Delete "$SMPROGRAMS\${SHORTCUT_FOLDER}\${SHORTCUT_FOLDER}.lnk"
  Delete "$SMPROGRAMS\${SHORTCUT_FOLDER}\Désinstaller.lnk"
  RMDir "$SMPROGRAMS\${SHORTCUT_FOLDER}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
un_files:
  !insertmacro RemovePayload "$INSTDIR"
  DetailPrint "Les conversations, réglages et projets personnels sont conservés."
  Call un.ReleaseProfileLock
SectionEnd
!endif
