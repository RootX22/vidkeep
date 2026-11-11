"""What the packaged Windows program runs: the window, or with --cli first, the vidkeep command line.

One program does both so the installer ships a single copy of Python and its libraries; the window
starts its own downloads as `Vidkeep.exe --cli get --json ...`.
"""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "--cli":
    del sys.argv[1]
    from vidkeep.cli import main

    sys.exit(main())

from vidkeep.gui import main

main()
