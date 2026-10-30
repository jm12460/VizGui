"""Exercise phone recovery using the real JavaScript and an isolated fake API."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_phone_interrupted_recovery():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not available")
    html = (Path(__file__).resolve().parents[1] / "vizgui/remote.html").read_text()
    recovery = html[html.index("function fmt(s)"):html.index("async function poll(){")]
    script = r'''
const assert=require('assert');
const nodes={};
function $(id){return nodes[id]||(nodes[id]={textContent:'',classList:{on:false,add(){this.on=true},remove(){this.on=false}}});}
let recoveryContext=null,recoverySeen=new Set(), calls=[], response={ok:true};
async function api(url, options){calls.push({url,options});return response;}
''' + recovery + r'''
(async()=>{
 const status={ok:true,server:{id:'server',instance_id:'instance'},target_index:2,
   interrupted:{id:'checkpoint',pos:65,title:'<img src=x>'}};
 syncRecovery(status);
 assert.equal($('recovery').classList.on,true);
 assert.equal($('recovery-detail').textContent,'<img src=x> · 1:05');
 assert.equal(calls.length,0);
 await dismissRecovery(); syncRecovery(status);
 assert.equal($('recovery').classList.on,false);
 assert.deepEqual(JSON.parse(calls[0].options.body),{kind:'dismiss_interrupted',id:'checkpoint',target_index:2});
 assert.equal(calls[0].options.headers['X-Viz-Instance-Id'],'instance');
 calls=[];
 const another={...status,interrupted:{...status.interrupted,id:'second'}};
 syncRecovery(another); syncRecovery({});
 assert.equal($('recovery').classList.on,false);
 await resumeRecovery(); assert.equal(calls.length,0);
 syncRecovery(another); assert.equal($('recovery').classList.on,true);
 await Promise.all([resumeRecovery(),resumeRecovery()]);
 assert.equal(calls.length,1);
 assert.deepEqual(JSON.parse(calls[0].options.body),{kind:'resume_interrupted',id:'second',target_index:2});
 assert.equal(calls[0].options.headers['X-Viz-Server-Id'],'server');
 assert.equal(calls[0].options.headers['X-Viz-Instance-Id'],'instance');
 const third={...status,interrupted:{...status.interrupted,id:'third'}};
 syncRecovery(third); syncRecovery({...third,interrupted:null});
 await resumeRecovery(); assert.equal(calls.length,1);
 syncRecovery(third); response={ok:false,error:'Server connection changed'};
 await resumeRecovery(); assert.equal($('recovery-error').textContent,'Server connection changed');
 syncRecovery(third); assert.equal($('recovery').classList.on,false);
 assert.equal(calls.length,2);
 const fourth={...status,interrupted:{...status.interrupted,id:'fourth'}};
 syncRecovery(fourth); response={}; await resumeRecovery();
 assert.match($('recovery-error').textContent,/Could not confirm/);
 syncRecovery({...status,server:{id:'server'}});
 assert.equal($('recovery').classList.on,false);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
