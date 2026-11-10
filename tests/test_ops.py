import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('kouhu_ops',Path(__file__).resolve().parents[1]/'ops/kouhubot.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)

class OpsTest(unittest.TestCase):
    def test_manifest_pins_repository_digest(self):
        data=ops.manifest(ops.ROOT)
        self.assertTrue(data['runtime_image'].startswith('ia3000/kouhubot@sha256:'))

    def test_cleanup_excludes_current_other_repository_and_used_images(self):
        rows=[{'Repository':'ia3000/kouhubot','Tag':tag} for tag in ('runtime-1','runtime-2','0.17.2')]
        rows.append({'Repository':'other/app','Tag':'latest'})
        def run(cmd,capture=False):
            if cmd[1:3]==['image','ls']:return '\n'.join(map(json.dumps,rows))
            if cmd[1:3]==['image','inspect']:
                return json.dumps([{'Id':{'runtime-1':'old','runtime-2':'current','0.17.2':'used'}[cmd[-1].split(':')[-1]]}])
            if cmd[1:3]==['ps','-aq']:return 'container' if cmd[-1]=='ancestor=used' else ''
            raise AssertionError(cmd)
        with patch.object(ops,'run',side_effect=run):
            self.assertEqual(ops.candidates(['docker'],'current'),{'ia3000/kouhubot:runtime-1':'old'})

    def test_old_digest_without_tag_is_cleanup_candidate(self):
        digest='sha256:'+'a'*64
        ref='ia3000/kouhubot@'+digest
        def run(cmd,capture=False):
            if cmd[1:3]==['image','ls']:return json.dumps({'Repository':'ia3000/kouhubot','Tag':'<none>','Digest':digest})
            if cmd[1:3]==['image','inspect']:return json.dumps([{'Id':'old'}])
            if cmd[1:3]==['ps','-aq']:return ''
            raise AssertionError(cmd)
        with patch.object(ops,'run',side_effect=run):
            self.assertEqual(ops.candidates(['docker'],'current'),{ref:'old'})

    def test_cleanup_default_never_deletes(self):
        with patch.object(ops,'inspect',return_value={'Image':'current'}),patch.object(ops,'candidates',return_value={'ia3000/kouhubot:runtime-1':'old'}),patch.object(ops,'run') as run:
            ops.cleanup(['docker'],False)
            run.assert_not_called()

    def test_failed_healthcheck_restores_old_image_without_cleanup(self):
        calls=[]
        def run(cmd,capture=False):
            calls.append(cmd)
            if cmd[1:3]==['info','--format']:return 'x86_64'
            if cmd[1:3]==['image','inspect']:return json.dumps([{'Id':'new'}])
            return ''
        with patch.object(ops,'run',side_effect=run),patch.object(ops,'inspect',return_value={'Image':'old'}),patch.object(ops,'check',side_effect=RuntimeError('unhealthy')),patch.object(ops,'cleanup') as cleanup:
            with self.assertRaisesRegex(RuntimeError,'unhealthy'):ops.upgrade(['docker'],ops.ROOT)
            cleanup.assert_not_called()
        self.assertIn(['docker','tag','old','kouhubot-runtime:local'],calls)
        self.assertNotIn(['docker','image','rm','kouhubot-runtime:rollback'],calls)

if __name__=='__main__':unittest.main()
