#!/bin/bash

rm -rf .data

./urantia-library/migrate_library.py --dir /Books --db /Books/urantia-library/webapp/backend/auth.db

find -name '.htaccess' -exec rm {} \;
find -name '000-browse.php' -exec rm {} \;
find -name '.covers' -exec rm -rf {} \;
find -type l -lname "*/default-cover.jpg" -exec rm {} \;

