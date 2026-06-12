import sys

if "--doctor" in sys.argv:
    from .doctor import run_doctor

    run_doctor()
else:
    from .app import main

    main()
