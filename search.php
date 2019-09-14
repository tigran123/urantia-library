<?php
ini_set('memory_limit','300M');
header("Content-Type: text/html; charset=utf-8");
$text = $_GET['text'];
$matches = '';
$time_start = microtime(TRUE);
$count = 0;

if (isset($text) && $text != '') {
    # obtain the current directory which the user may have entered into while browsing
    preg_match('/http:\/\/.*\/b\/(.*)$/u', $_SERVER["HTTP_REFERER"], $browsedir);
    $bdir = "../b/" . $browsedir[1];

    # first look inside the .htaccess files for match inside AddDescription "..."
    $Directory = new RecursiveDirectoryIterator($bdir, FilesystemIterator::SKIP_DOTS);
    $filter = new RecursiveCallbackFilterIterator($Directory, function($current, $key, $iterator) {
        if ($current->isDir())
            return TRUE;
        else 
            return $current->getFilename() === '.htaccess';
    });
    $regex = '/^AddDescription ".*'.$text.'.*" *(.*)$/iu';
    $objects = new RecursiveIteratorIterator($filter);
    foreach($objects as $info) {
        $hits = [];
        $file = $info->getPathname();
        $dir = pathinfo($file)['dirname'];
        $lines = file($file);
        foreach($lines as $line) {
            if (preg_match($regex, $line, $hits)) {
                $name = ltrim($dir.'/'.$hits[1],'.');
                $matches .= "<b>[" . ++$count . "]&nbsp;<a href='/b" . $name . "'></b>" . $name . "</a> | <a href='/b/" . $dir ."'>UP</a>";
                if (pathinfo($name, PATHINFO_EXTENSION) == 'djvu') 
                    $matches .= " | <a href='/b/urantia-library/djview.php?file=..".$name."'>VIEW</a><br>";
                else
                    $matches .= "<br>";
            }
        }
    }

    # now look for match anywhere in the pathname
    $Directory = new RecursiveDirectoryIterator($bdir, FilesystemIterator::SKIP_DOTS);
    $Iterator = new RecursiveIteratorIterator($Directory);
    $regex = '/'.$text.'/i';
    $objects = new RegexIterator($Iterator, $regex, RecursiveRegexIterator::GET_MATCH);
    foreach($objects as $name => $object) {
        $name = ltrim($name, '.');
        $info = pathinfo($name);
        if (strpos($matches, $name) != FALSE) continue; # we have already found this book from .htaccess
        if ($info['basename'] == 'header.html' || $info['basename'] == 'footer.html' || $info['basename'] == '.htaccess' || 
             $info['basename'] == '000-browse.php' ||
            (strpos($info['dirname'],'.covers') != FALSE) || (strpos($info['dirname'],'urantia-library') != FALSE) ||
            (strpos($info['dirname'],'Websites') != FALSE) || (strpos($info['dirname'],'.authors') != FALSE)) continue;
        $matches .= "[" . ++$count . "]&nbsp;<a href='/b" . $name . "'>" . $name . "</a> | <a href='/b" . $info['dirname'] ."'>UP</a>";
        if ($info['extension'] == 'djvu') 
            $matches .= " | <a href='/b/urantia-library/djview.php?file=..".$name."'>VIEW</a><br>";
        else
            $matches .= "<br>";
    }
} else
    $matches = 'Empty search request';
$time = sprintf("%.1f s", microtime(TRUE) - $time_start);
echo json_encode(['matches' => $count.' matches in '.$time.'. Bold <b>[count]</b> indicates match in .htaccess.<br>'.$matches]);
flush();
exit;
?>
