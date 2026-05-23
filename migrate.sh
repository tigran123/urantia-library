#!/bin/bash

rm -rf /Books/.data \
       /Books/Art/ \
       /Books/Dictionaries/ \
       /Books/Encyclopedias/ \
       /Books/fb2/ \
       /Books/Fiction/ \
       /Books/Grammars/ \
       /Books/History/ \
       /Books/Institut-russkoy-civilizacii/ \
       /Books/Law/ \
       /Books/Manuscripts/ \
       /Books/Music/ \
       /Books/Recipes/ \
       /Books/Religions/ \
       /Books/Science/ \
       /Books/Urantia/ \
       /Books/Zhurnaly/

/Books/urantia-library/webapp/backend/initdb.sh

export PYTHONUNBUFFERED=1

/Books/urantia-library/migrate_library.py --src /Books-full --target /Books --db /Books/.data/db/lib.db --exclude-file=/Books-full/exclude.txt
