#!/usr/bin/env python3
"""Build the exact asset consumed by the Pi updater; run tests before publishing."""
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from update_manager import FILES, metadata
if __name__=='__main__':
    output=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dist/thermal-prep-pi64.zip'
    output.parent.mkdir(parents=True,exist_ok=True)
    metadata(ROOT)
    with ZipFile(output,'w',ZIP_DEFLATED) as bundle:
        for name in FILES:bundle.write(ROOT/name,name)
    print(output)
