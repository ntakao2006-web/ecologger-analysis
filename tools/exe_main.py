"""exe（PyInstaller）の入口。中身は ecoanalysis.app.main と同じ"""
import sys

from ecoanalysis.app import main

if __name__ == '__main__':
    sys.exit(main())
