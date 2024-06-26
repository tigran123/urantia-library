#!/usr/bin/env python3

"""
    register-books.py --- Update books registry if necessary
    Author: Tigran Aivazian <aivazian.tigran@gmail.com>
    License: GPLv3
"""

import os, sys, re
from argparse import ArgumentParser as argp

p = argp(description="Register Books in URANTIA Library")
p.add_argument("-d",  action="store", help="Top-level books directory", dest="rootdir", required=True)
p.add_argument("-v",  action="store_true", help="Print verbose (non-critical) messages", dest="verbose")
p.add_argument("-n",  action="store_true", help="Dry run, don't modify anything", dest="dryrun")
p.add_argument("-f",  action="store_true", help="Force rebuilding existing covers", dest="force")
args = p.parse_args()

def pr_msg(str):
    print("register-books: " + str)

def pr_exit(str):
    pr_msg("ERROR: " + str)
    sys.exit()

def makecover(dirname, filename, cimage):
    coverdir = dirname + '/.covers/'
    if not args.dryrun:
        if os.system('convert -geometry 300 ' + cimage + ' ' + coverdir + filename + '.jpg'):
            pr_msg('FAILED: convert -geometry 300' + cimage + ' ' + coverdir + filename + '.jpg')
        if os.remove(cimage):
            pr_exit('os.remove()')

def generate_cover(dirname, filename):
    if args.verbose: print("Processing \"%s\" in \"%s\": " % (filename, dirname), end='')
    ext = 'ppm'
    coverdir = dirname + '/.covers/'
    if os.makedirs(coverdir, exist_ok=True): pr_exit('os.makedirs()')
    if filename.endswith('.djvu'):
        if not args.dryrun:
            if os.system('ddjvu -format=ppm -pages=1 ' + dirname + '/' + filename + ' /tmp/cover.ppm'):
                pr_msg('ddjvu failed: ' + filename)
                return
    elif filename.endswith(('.pdf', '.PDF')):
        if not args.dryrun:
            if os.system('pdftoppm -singlefile ' + dirname + '/' + filename + ' /tmp/cover'):
                pr_msg('pdftoppm failed: ' + filename)
                return
    elif filename.endswith(('.fb2.zip','.epub','.mobi','.azw','.prc','.docx','.odt','.azw3','.html')):
        if not args.dryrun:
            if os.system('ebook-meta ' + dirname + '/' + filename + ' --get-cover=/tmp/cover.jpg'):
                pr_msg('ebook-meta failed: ' + filename)
                return
            if not os.path.isfile('/tmp/cover.jpg'):
                if args.verbose: print("Using /b/default-cover.jpg for %s in %s" % (filename, dir))
                if os.system('ln -sf /b/urantia-library/default-cover.jpg ' + coverdir + filename + '.jpg'):
                   pr_exit('ln -sf')
                return
        ext = 'jpg'
    else:
        if args.verbose: print("Using default-cover.jpg for %s in %s" % (filename, dir))
        if not args.dryrun:
            if os.system('ln -sf /b/urantia-library/default-cover.jpg ' + coverdir + filename + '.jpg'):
                pr_exit('ln -sf')
        return
    makecover(dirname, filename, '/tmp/cover.' + ext)
    if args.verbose: print("OK")

for top,dirs,files in os.walk(args.rootdir):

    # do not even enter these subdirectories
    excludeset = {'Incoming', 'Subjects', 'Recommended-Books', 'Series', 'Music', 'Scores', '.covers', '.authors', 'Websites', 'urantia-library', 'Html-Docs'}
    for d in excludeset:
        if d in dirs: dirs.remove(d)

    if top == '.': continue # don't mess with the root directory

    browsephp = os.path.join(top, '000-browse.php')
    if not os.path.exists(browsephp) and not os.path.islink(browsephp): # broken links are okay
        if args.verbose: print("Creating symlink 000-browse.php => /b/000-browse.php in %s" % top)
        if not args.dryrun: os.symlink("/b/000-browse.php", os.path.join(top, "000-browse.php"))
    hta = os.path.join(top, '.htaccess')
    if not os.path.exists(hta): os.system("touch " + hta) # create '.htaccess' file
    with open(hta, 'r') as fh: htaccess = fh.read() # read the whole .htaccess

    # Check if all files in this directory are registered and have (registered) covers
    for f in files:
        if f == ".htaccess" or f == "000-browse.php": continue

        relcimage = os.path.join(top, '.covers', f + '.jpg' )
        if not os.path.exists(relcimage) and not os.path.islink(relcimage) or args.force:
            generate_cover(top, f)

    # append AddDescription if necessary
    for f in dirs + files:
        if f == ".htaccess" or f == "000-browse.php": continue
        if not re.search("AddDescription \".*\" " + re.escape(f), htaccess):
            print("AddDescription for \"%s\" in \"%s\"" % (f, top))
            if not args.dryrun:
                with open(hta, 'a') as fh: fh.write('AddDescription "' + f + '" ' + f + '\n')
