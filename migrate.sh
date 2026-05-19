#!/bin/bash

rm -rf /Books/.data

/Books/urantia-library/webapp/backend/initdb.sh

export PYTHONUNBUFFERED=1

/Books/urantia-library/migrate_library.py --src /Books-test --target /Books --db /Books/urantia-library/webapp/backend/auth.db --exclude-file=/Books-test/exclude.txt
