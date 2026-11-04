#!/usr/bin/env python3
"""Exercise the shipped GGUF with the same upstream Linux engine for build QA."""
import json, pathlib, secrets, subprocess, time, urllib.error, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
QA=ROOT/'qa';QA.mkdir(exist_ok=True)
ENGINE=ROOT/'tooldeps/llama-linux/llama-b12089/llama-server'
MODEL=ROOT/'build/payload/wad/model/Qwen3.5-4B-Q4_K_M.gguf'
key=secrets.token_hex(16)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(path,body=None):
    req=urllib.request.Request('http://127.0.0.1:18741'+path,data=json.dumps(body).encode() if body else None,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with opener.open(req,timeout=240) as r:return json.load(r)
def main():
    log=(QA/'model-server.log').open('w')
    cmd=[str(ENGINE),'-m',str(MODEL),'--alias','qwen3.5-4b','-c','32768','-ngl','0','--jinja','--parallel','1','--offline','--host','127.0.0.1','--port','18741','--api-key',key,'--no-webui','--reasoning','off','-t','4','-tb','6','-b','256','-ub','128']
    p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
    results={'engine':'llama.cpp b12089 Linux counterpart','model':'Qwen3.5-4B-Q4_K_M','context':32768,'platform':'Linux functional check, not Windows hardware qualification'}
    started=time.monotonic()
    try:
        for attempt in range(240):
            if p.poll() is not None:raise RuntimeError('Engine exited: '+str(p.returncode))
            try:
                if request('/health').get('status')=='ok':break
            except (OSError,urllib.error.HTTPError):pass
            time.sleep(.5)
        else:raise RuntimeError('Engine startup timed out')
        results['startup_seconds']=round(time.monotonic()-started,2)
        print('ENGINE READY',results['startup_seconds'],flush=True)
        results['models']=request('/v1/models')
        results['text']=request('/v1/chat/completions',{'model':'qwen3.5-4b','messages':[{'role':'user','content':'Réponds exactement par : LOCAL_OK'}],'temperature':0,'max_tokens':32})
        print('TEXT',results['text']['choices'][0]['message'],flush=True)
        tools=[{'type':'function','function':{'name':'read_file','description':'Lire un fichier texte du projet.','parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path'],'additionalProperties':False}}}]
        msg=[{'role':'user','content':'Utilise obligatoirement read_file pour lire README.md. Ne devine pas son contenu.'}]
        first=request('/v1/chat/completions',{'model':'qwen3.5-4b','messages':msg,'tools':tools,'tool_choice':'required','temperature':0,'max_tokens':256})
        results['tool_call']=first
        calls=first['choices'][0]['message'].get('tool_calls',[])
        assert calls and calls[0]['function']['name']=='read_file',first
        args=json.loads(calls[0]['function']['arguments']);assert args['path']=='README.md',args
        msg.append(first['choices'][0]['message'])
        msg.append({'role':'tool','tool_call_id':calls[0]['id'],'content':'Le code de validation du projet est FENETRE_42.'})
        results['tool_result']=request('/v1/chat/completions',{'model':'qwen3.5-4b','messages':msg,'tools':tools,'tool_choice':'none','temperature':0,'max_tokens':100})
        assert 'FENETRE_42' in results['tool_result']['choices'][0]['message']['content'],results['tool_result']
        results['ok']=True
        print('TOOL CALL AND RESULT OK',flush=True)
    except Exception as exc:
        results['ok']=False;results['error']=str(exc);raise
    finally:
        (QA/'model-smoke.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
        p.terminate()
        try:p.wait(timeout=10)
        except subprocess.TimeoutExpired:p.kill();p.wait()
        log.close()

if __name__=='__main__':main()
