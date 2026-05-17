#!/bin/bash

rm -rf /Books/.data

/Books/urantia-library/webapp/backend/initdb.sh

export PYTHONUNBUFFERED=1

/Books/urantia-library/migrate_library.py --src /Books-test --target /Books --db /Books/urantia-library/webapp/backend/auth.db --exclude-file=/Books-test/exclude.txt

#find /Books -name '.htaccess' -exec rm {} \;
#find /Books -name '000-browse.php' -exec rm {} \;
#find /Books -name '.covers' -exec rm -rf {} \;
#find /Books -type l -lname "*/default-cover.jpg" -exec rm {} \;
