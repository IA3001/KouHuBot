"""Exercise updater against disposable fake Git/Docker commands, no containers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'update.sh'

class UpdaterTest(unittest.TestCase):
    def test_syntax_and_help(self):
        subprocess.run(['bash','-n',str(SCRIPT)],check=True)
        result=subprocess.run(['bash',str(SCRIPT),'--help'],capture_output=True,text=True,check=True)
        self.assertIn('不拉镜像',result.stdout)

    def exercise(self,dirty=False,pull_failure=False):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);repo=root/'repo';bin_dir=root/'bin'
            (repo/'.git').mkdir(parents=True);bin_dir.mkdir()
            (repo/'deploy').mkdir();(repo/'plugins/astrbot_plugin_kouhu_oj').mkdir(parents=True)
            for name in ('docker-compose.yml','compose.plugin.yml'):(repo/'deploy'/name).touch()
            main=repo/'plugins/astrbot_plugin_kouhu_oj/main.py';main.write_text('test source')
            log=root/'calls'
            git=bin_dir/'git';git.write_text('''#!/bin/bash
printf 'git %s\\n' "$*" >> "$CALL_LOG"
case "$*" in
'rev-parse --show-toplevel') echo "$TEST_REPO";;
'rev-parse --git-path kouhu-update.lock') echo "$TEST_REPO/.git/kouhu-update.lock";;
'status --porcelain') [[ "$DIRTY" != 1 ]] || echo ' M main.py'; exit 0;;
'branch --show-current') echo main;;
'rev-parse --abbrev-ref --symbolic-full-name @{u}') echo origin/main;;
'rev-parse HEAD') echo abcdef123456;;
'pull --ff-only') [[ "$PULL_FAILURE" != 1 ]];;
*) exit 5;;
esac
''');git.chmod(0o755)
            docker=bin_dir/'docker';docker.write_text('''#!/bin/bash
printf 'docker %s\\n' "$*" >> "$CALL_LOG"
case "$*" in
info|'compose version') exit 0;;
'inspect --format {{.Image}} astrbot') echo sha256:123456;;
'inspect --format {{.Id}} astrbot') echo existingcontainer;;
'inspect --format {{.State.Status}} astrbot') echo running;;
'exec astrbot python -c '*) sha256sum "$TEST_REPO/plugins/astrbot_plugin_kouhu_oj/main.py" | cut -d ' ' -f 1;;
logs*) exit 0;;
tag*) exit 0;;
compose*) exit 0;;
*) exit 6;;
esac
''');docker.chmod(0o755)
            env=dict(os.environ,PATH=str(bin_dir)+':'+os.environ['PATH'],TEST_REPO=str(repo),CALL_LOG=str(log),DIRTY=str(int(dirty)),PULL_FAILURE=str(int(pull_failure)))
            result=subprocess.run(['bash',str(SCRIPT),str(repo)],capture_output=True,text=True,env=env)
            return result,log.read_text()

    def test_success_uses_existing_image_and_no_download(self):
        result,log=self.exercise()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('up -d --pull never --no-build astrbot',log)
        self.assertIn('docker tag sha256:123456 kouhubot-runtime:local',log)
        self.assertIn('restart astrbot',log)
        self.assertNotIn('docker pull',log)
        self.assertNotIn('docker build',log)

    def test_dirty_checkout_stops_before_docker(self):
        result,log=self.exercise(dirty=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('docker ',log)

    def test_pull_failure_never_mutates_container(self):
        result,log=self.exercise(pull_failure=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('docker tag',log)
        self.assertNotIn('up -d',log)
        self.assertNotIn('restart',log)

if __name__=='__main__': unittest.main()
