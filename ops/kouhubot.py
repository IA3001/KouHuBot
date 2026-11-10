#!/usr/bin/env python3
"""Host maintenance CLI. No registry delete credentials, build or volume prune."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]

def run(args, capture=False):
    return subprocess.run(args,check=True,text=True,stdout=subprocess.PIPE if capture else None).stdout

def docker_command():
    if subprocess.run(['docker','info'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
        return ['docker']
    run(['sudo','-v'])
    run(['sudo','docker','info'],capture=True)
    return ['sudo','docker']

def inspect(docker):
    return json.loads(run(docker+['inspect','astrbot'],True))[0]

def manifest(root):
    data=json.loads((root/'deploy/runtime.json').read_text())
    if data.get('schema')!=1 or not re.fullmatch(r'runtime-[1-9]\d*',data.get('runtime_version','')):
        raise ValueError('环境清单版本不合法')
    if not re.fullmatch(r'ia3000/kouhubot@sha256:[0-9a-f]{64}',data.get('runtime_image','')):
        raise ValueError('环境清单必须固定官方仓库的镜像摘要')
    if data.get('platform')!='linux/amd64': raise ValueError('当前基础环境仅验证linux/amd64')
    return data

def compose(docker,root):
    return docker+['compose','--project-directory',str(root/'deploy'),'-f',str(root/'deploy/docker-compose.yml'),'-f',str(root/'deploy/compose.plugin.yml')]

def check(docker,root,timeout=45):
    expected=hashlib.sha256((root/'plugins/astrbot_plugin_kouhu_oj/main.py').read_bytes()).hexdigest()
    code='import hashlib;from pathlib import Path;from data.plugins.astrbot_plugin_kouhu_oj.main import KouHuOJ;print(hashlib.sha256(Path("/AstrBot/data/plugins/astrbot_plugin_kouhu_oj/main.py").read_bytes()).hexdigest())'
    deadline=time.monotonic()+timeout
    while True:
        state=inspect(docker)
        if state['State']['Status']=='running':
            result=subprocess.run(docker+['exec','astrbot','python','-c',code],capture_output=True,text=True)
            if result.returncode==0 and result.stdout.strip().splitlines()[-1:]==[expected]:
                print('检查通过：容器运行、插件可导入、源码哈希一致。QQ连接和模型需单独验证。')
                return
        if time.monotonic()>=deadline:raise RuntimeError('容器/插件检查失败；查看 docker logs astrbot')
        time.sleep(2)

def candidates(docker,current):
    rows=run(docker+['image','ls','ia3000/kouhubot','--digests','--format','{{json .}}'],True)
    found={}
    for row in rows.splitlines():
        item=json.loads(row)
        if item['Repository']!='ia3000/kouhubot':continue
        if item['Tag']=='<none>':
            digest=item.get('Digest','')
            if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):continue
            ref=item['Repository']+'@'+digest
        else:
            ref=item['Repository']+':'+item['Tag']
        image=json.loads(run(docker+['image','inspect',ref],True))[0]
        if image['Id']==current:continue
        users=run(docker+['ps','-aq','--filter','ancestor='+image['Id']],True).strip()
        if not users:found[ref]=image['Id']
    return found

def cleanup(docker,apply):
    current=inspect(docker)['Image']
    targets=candidates(docker,current)
    for ref,image in targets.items():print('旧镜像候选：',ref,image)
    if not targets:print('没有可清理的KouHuBot旧镜像。')
    if not apply:
        print('仅预览。确认后运行 cleanup --apply；不清理数据卷、其他仓库或共享构建缓存。')
        return
    for ref,expected in targets.items():
        # Revalidate immediately before mutation; never force-delete in-use images.
        actual=json.loads(run(docker+['image','inspect',ref],True))[0]['Id']
        if actual!=expected or actual==inspect(docker)['Image']:raise RuntimeError('镜像状态变化，停止清理')
        if run(docker+['ps','-aq','--filter','ancestor='+actual],True).strip():continue
        run(docker+['image','rm',ref])

def upgrade(docker,root):
    data=manifest(root)
    if run(docker+['info','--format','{{.Architecture}}'],True).strip() not in ('x86_64','amd64'):
        raise RuntimeError('该环境镜像仅验证amd64，停止升级')
    comp=compose(docker,root)
    run(comp+['config','--quiet'])
    old=inspect(docker)['Image']
    run(docker+['tag',old,'kouhubot-runtime:rollback'])
    print('环境升级：',data['runtime_version'],data['runtime_image'])
    run(docker+['pull',data['runtime_image']])
    new=json.loads(run(docker+['image','inspect',data['runtime_image']],True))[0]['Id']
    try:
        run(docker+['tag',new,'kouhubot-runtime:local'])
        run(comp+['up','-d','--pull','never','--no-build','--force-recreate','astrbot'])
        check(docker,root)
    except BaseException:
        print('环境升级检查失败，恢复旧镜像。不会清理旧镜像或数据。',file=sys.stderr)
        run(docker+['tag',old,'kouhubot-runtime:local'])
        run(comp+['up','-d','--pull','never','--no-build','--force-recreate','astrbot'])
        raise
    # Explicit major upgrade authorizes project-only cleanup after success.
    run(docker+['image','rm','kouhubot-runtime:rollback'])
    cleanup(docker,True)
    print('环境升级完成；仅清理无容器引用的KouHuBot旧标签，远端标签由发布端管理。')

def main():
    parser=argparse.ArgumentParser(description='KouHuBot宿主机运维：日常不拉镜像，环境升级需显式命令')
    parser.add_argument('--repo',type=Path,default=ROOT)
    subs=parser.add_subparsers(dest='command',required=True)
    for cmd in ('update','status','check','upgrade-runtime'):subs.add_parser(cmd)
    clean=subs.add_parser('cleanup');clean.add_argument('--apply',action='store_true')
    args=parser.parse_args();root=args.repo.resolve()
    if not (root/'deploy/docker-compose.yml').is_file():raise RuntimeError('请指定完整KouHuBot仓库')
    if args.command=='update':
        run(['bash',str(root/'update.sh'),str(root)]);return
    docker=docker_command()
    if args.command=='status':
        state=inspect(docker)
        print(json.dumps({'image':state['Image'],'state':state['State']['Status'],'expected_runtime':manifest(root)},ensure_ascii=False,indent=2));return
    if args.command=='check':check(docker,root);return
    gitdir=Path(run(['git','-C',str(root),'rev-parse','--absolute-git-dir'],True).strip())
    with (gitdir/'kouhu-update.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.command=='cleanup':cleanup(docker,args.apply)
        elif args.command=='upgrade-runtime':upgrade(docker,root)

if __name__=='__main__':
    try:main()
    except (Exception,KeyboardInterrupt) as exc:
        print(f'运维失败：{type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(1)
