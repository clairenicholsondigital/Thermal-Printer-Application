"""GitHub-only, per-user application updates. No shell hooks or package installs."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen, build_opener, ProxyHandler
from zipfile import ZipFile

REPO = 'clairenicholsondigital/Thermal-Printer-Application'
ASSET = 'thermal-prep-pi64.zip'
LIMIT = 20 * 1024 * 1024
FILES = ('app.py','converter.py','bluetooth_link.py','printer_driver.py','printer_service.py',
         'launch.py','update_manager.py','release.json','installer.py','install.sh','driver_install.py',
         'README.md','VALIDATION.md','static/app.js','static/printer.js','static/updates.js',
         'static/style.css','static/index.html')


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):
        raise ValueError('Invalid release version.')
    return tuple(map(int,value.split('.')))


def metadata(root):
    data=json.loads((Path(root)/'release.json').read_text())
    version(data['version'])
    if data.get('architecture')!='aarch64' or data.get('dependencies')!=1 or data.get('updater_protocol')!=1:
        raise ValueError('This release needs a one-time installer update for new system requirements.')
    return data


def sync_dir(path):
    descriptor=os.open(path,os.O_RDONLY | os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def sync_tree(root):
    root=Path(root)
    for path in root.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:os.fsync(stream.fileno())
    for path in sorted((p for p in root.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
        sync_dir(path)
    sync_dir(root)
    sync_dir(root.parent)


def atomic_bytes(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    temp=Path(name)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        temp.replace(path)
        sync_dir(path.parent)
        sync_dir(path.parent.parent)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path,data):
    atomic_bytes(path,json.dumps(data).encode())


def durable_move(source,target):
    source=Path(source);target=Path(target)
    source.rename(target)
    sync_dir(source.parent)
    if target.parent!=source.parent:sync_dir(target.parent)


def durable_unlink(path):
    path=Path(path);path.unlink(missing_ok=True);sync_dir(path.parent)


def download(url,limit=LIMIT):
    with urlopen(Request(url,headers={'User-Agent':'Thermal-Prep-Updater','Accept':'application/vnd.github+json'}),timeout=30) as response:
        data=response.read(limit+1)
    if len(data)>limit:raise ValueError('Update download is too large.')
    return data


def latest():
    try:
        release=json.loads(download(f'https://api.github.com/repos/{REPO}/releases/latest',1024*1024))
    except HTTPError as exc:
        if exc.code==404:return None
        raise
    tag=release['tag_name'];number=tag.removeprefix('v');version(number)
    if release.get('draft') or release.get('prerelease'):raise ValueError('Only stable releases are supported.')
    asset=next((a for a in release['assets'] if a['name']==ASSET),None)
    if not asset:raise ValueError('The latest release has no Pi update package yet. Try again later.')
    digest=asset.get('digest','')
    url=asset['browser_download_url']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest) or url!=f'https://github.com/{REPO}/releases/download/{tag}/{ASSET}':
        raise ValueError('GitHub has not supplied a verified update package.')
    return {'version':number,'notes':str(release.get('body') or '')[:12000],
            'url':url,'sha256':digest.split(':')[1]}


def stage(root,release):
    root=Path(root);state=root.parent/'.thermal-prep-updates';state.mkdir(exist_ok=True)
    data=download(release['url'])
    if hashlib.sha256(data).hexdigest()!=release['sha256']:raise ValueError('Update checksum does not match. Nothing installed.')
    folder=Path(tempfile.mkdtemp(prefix='candidate-',dir=state))
    try:
        with ZipFile(io.BytesIO(data)) as bundle:
            entries=bundle.infolist()
            names=[e.filename for e in entries]
            if len(names)!=len(set(names)) or set(names)!=set(FILES):raise ValueError('Unexpected files in update package.')
            if sum(e.file_size for e in entries)>LIMIT:raise ValueError('Expanded update is too large.')
            for entry in entries:
                name=PurePosixPath(entry.filename)
                if name.is_absolute() or '..' in name.parts or stat.S_ISLNK(entry.external_attr>>16):
                    raise ValueError('Unsafe update archive.')
                target=folder/entry.filename;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(bundle.read(entry))
        if metadata(folder)['version']!=release['version']:raise ValueError('Package version does not match the release.')
        # Syntax-check without executing downloaded modules.
        for file in folder.glob('*.py'):compile(file.read_bytes(),str(file),'exec')
        if (root/'drivers').exists():shutil.copytree(root/'drivers',folder/'drivers')
        sync_tree(folder)
        return folder
    except Exception:
        shutil.rmtree(folder,ignore_errors=True);raise


class Updates:
    def __init__(self,server,root):
        self.server=server;self.root=Path(root);self.lock=threading.RLock()
        self.state_dir=self.root.parent/'.thermal-prep-updates'
        self.release=None
        self.state={'status':'idle','message':'Check GitHub for a new version.'}
        self.enabled=(os.environ.get('THERMAL_MANAGED')=='1' and
                      self.root==Path.home()/'.local/share/thermal-prep' and
                      platform.machine().lower() in ('aarch64','arm64') and not server.preview)
        # Older installations keep the supervisor outside the versioned app folder.
        # Replace its file atomically, but do not pretend a running old process changed.
        self.restart_required=self.enabled and os.environ.get('THERMAL_DURABLE_SUPERVISOR')!='1'
        if self.restart_required:
            atomic_bytes(self.root.parent/'thermal-prep-manager.py',(self.root/'update_manager.py').read_bytes())
            self.enabled=False
            self.state={'status':'idle','message':'Restart the Pi once to activate safer updates. Your documents and printer settings are retained.'}
        receipt=self.state_dir/'result.json'
        if self.enabled and receipt.exists():
            try:self.state=json.loads(receipt.read_text())
            except (OSError,ValueError):pass

    def public(self):
        with self.lock:
            return {**self.state,'current':metadata(self.root)['version'],'enabled':self.enabled,
                    'restart_required':self.restart_required,'available':self.release,'can_restore':self.enabled and (self.state_dir/'previous').is_dir()}

    def start(self,action):
        if action not in ('check','install','restore'):raise ValueError('Unknown update action.')
        with self.lock:
            if self.state['status'] in ('checking','downloading','restarting'):raise ValueError('An update operation is already running.')
            if action!='check':
                if not self.enabled:raise ValueError('Run the updated installer once on your 64-bit Pi, then open Thermal Prep from its menu.')
                if self.server.printer.active():raise ValueError('Wait for the current printer operation to finish.')
                if action=='install' and (not self.release or version(self.release['version'])<=version(metadata(self.root)['version'])):
                    raise ValueError('Check for a newer release first.')
                if action=='restore' and not (self.state_dir/'previous').is_dir():raise ValueError('No previous version is available.')
                self.server.updating=True
            self.state={'status':'checking' if action=='check' else 'downloading','message':'Checking GitHub…' if action=='check' else 'Preparing update. Keep the Pi powered on…'}
            threading.Thread(target=self.work,args=(action,),daemon=True).start()
        return self.public()

    def work(self,action):
        try:
            if action=='check':
                release=latest()
                with self.lock:
                    self.release=release
                    newer=release and version(release['version'])>version(metadata(self.root)['version'])
                    self.state={'status':'ready' if newer else 'idle','message':'Update available.' if newer else 'You have the latest available version.'}
                return
            candidate=stage(self.root,self.release) if action=='install' else self.state_dir/'previous'
            atomic_json(self.state_dir/'pending.json',{'candidate':candidate.name,'version':metadata(candidate)['version']})
            with self.lock:self.state={'status':'restarting','message':'Restarting the app. Your printer settings are retained…'}
            with self.server.operation_gate:
                self.server.shutdown()
        except Exception as exc:
            with self.lock:self.state={'status':'failed','message':f'Update stopped: {exc}. Your installed app is unchanged.'}
            self.server.updating=False


def switch(root,candidate):
    """Keep a recovery journal until the new process passes its health check."""
    root=Path(root);state=root.parent/'.thermal-prep-updates';old=state/'recover'
    if old.exists():raise ValueError('An earlier update needs recovery.')
    sync_tree(candidate)
    sync_tree(root)
    atomic_json(state/'journal.json',{'candidate':candidate.name})
    durable_move(root,old)
    durable_move(candidate,root)


def recover(root):
    root=Path(root);state=root.parent/'.thermal-prep-updates'
    if (state/'journal.json').exists():
        old=state/'recover'
        if old.exists():
            if root.exists():shutil.rmtree(root)
            durable_move(old,root)
        durable_unlink(state/'journal.json')
        durable_unlink(state/'pending.json')
        atomic_json(state/'result.json',{'status':'failed','message':'The update could not start. The previous app was restored.'})


def healthy(number,timeout=20):
    opener=build_opener(ProxyHandler({}));deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            with opener.open('http://127.0.0.1:8877/health',timeout=1) as response:
                data=json.load(response)
            if data.get('app')=='thermal-prep' and data.get('version')==number:return True
        except Exception:pass
        time.sleep(.25)
    return False


def recover_locked(root):
    root=Path(root);state=root.parent/'.thermal-prep-updates';state.mkdir(exist_ok=True)
    with (state/'supervisor.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        recover(root)


def apply_pending(root):
    root=Path(root);state=root.parent/'.thermal-prep-updates';pending=state/'pending.json'
    if not pending.exists():return
    try:
        request=json.loads(pending.read_text());name=request['candidate']
        if name!='previous' and not re.fullmatch(r'candidate-[a-z0-9_]+',name):raise ValueError('Invalid staged update.')
        candidate=state/name
        if metadata(candidate)['version']!=request['version']:raise ValueError('Staged update changed.')
        switch(root,candidate)
        durable_unlink(pending)
    except Exception:
        recover(root)
        durable_unlink(pending)
        atomic_json(state/'result.json',{'status':'failed','message':'The update could not be installed. The previous app was retained.'})


def supervise(root):
    root=Path(root).resolve();state=root.parent/'.thermal-prep-updates';state.mkdir(exist_ok=True)
    with (state/'supervisor.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        recover(root)
        while True:
            apply_pending(root)
            process=subprocess.Popen([sys.executable,str(root/'app.py')],cwd=root,env={**os.environ,'THERMAL_MANAGED':'1','THERMAL_DURABLE_SUPERVISOR':'1'})
            if (state/'journal.json').exists():
                if healthy(metadata(root)['version']):
                    previous=state/'previous'
                    if previous.exists():shutil.rmtree(previous)
                    durable_move(state/'recover',previous)
                    durable_unlink(state/'journal.json')
                    atomic_json(state/'result.json',{'status':'done','message':'App updated successfully. Reopen your document to print.'})
                else:
                    process.terminate()
                    try:process.wait(10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                    recover(root);continue
            process.wait()
            if not (state/'pending.json').exists():return


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--supervise':supervise(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=='--recover':recover_locked(sys.argv[2])
    else:raise SystemExit('Use the Thermal Prep launcher.')
