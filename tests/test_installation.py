from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import driver_install
import installer


class InstallationTests(unittest.TestCase):
    def test_installer_copies_bluetooth_app_and_quotes_desktop_path(self):
        with tempfile.TemporaryDirectory(prefix='thermal prep 100% ') as tmp:
            home=Path(tmp);calls=[]
            def run(args,**kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args,0,stdout='  Candidate: 2.4.7\n')
            with patch.object(installer.os,'geteuid',return_value=1000), patch.object(installer.Path,'home',return_value=home), patch.object(installer.shutil,'which',side_effect=lambda name:'/usr/bin/'+name), patch.object(installer.subprocess,'run',side_effect=run), patch.object(installer,'install_driver') as driver, patch.object(sys,'argv',['installer.py']),redirect_stdout(io.StringIO()):
                installer.main()
            target=home/'.local/share/thermal-prep'
            for name in ('app.py','bluetooth_link.py','printer_driver.py','printer_service.py','static/printer.js','static/app.js','README.md'):
                self.assertTrue((target/name).is_file(),name)
            driver.assert_called_once_with(target,None)
            packages=next(args for args in calls if args[:3]==['sudo','apt-get','install'])
            for package in ('python3-dbus','python3-gi','bluez','cups','libcupsimage2t64'):
                self.assertIn(package,packages)
            desktop=(home/'.local/share/applications/thermal-prep.desktop').read_text()
            self.assertIn('100%%',desktop)
            self.assertIn('Exec=/usr/bin/python3 "',desktop)

    def test_modified_driver_archive_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'driver.zip';archive.write_bytes(b'not the vendor archive')
            with patch.object(driver_install.platform,'machine',return_value='aarch64'):
                with self.assertRaisesRegex(ValueError,'differs'):driver_install.install_driver(root/'app',archive)
            self.assertFalse((root/'app/drivers').exists())

    def test_unsupported_architecture_rejected_before_download(self):
        with patch.object(driver_install.platform,'machine',return_value='armv7l'),patch.object(driver_install,'urlopen') as download:
            with self.assertRaises(ValueError):driver_install.install_driver('.')
            download.assert_not_called()


if __name__=='__main__':unittest.main()
