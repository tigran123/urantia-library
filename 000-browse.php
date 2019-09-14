<?php
$style = 'width:300px; height: 400px float:left; padding:1px; border: solid black 1px;';

$dir = new RecursiveDirectoryIterator('.');
$ite = new RecursiveIteratorIterator($dir);
$files = new RegexIterator($ite, '/^.*\.covers\/.*\.jpg/', RegexIterator::GET_MATCH);

foreach($files as $image => $object) {
    $filename = str_replace(['/.covers','.jpg'],'', $image);
    $info = pathinfo($filename);
    $bookname = $info['basename'];
    $htalines = file($info['dirname'].'/.htaccess');
    $title = '';
    foreach($htalines as $htaline) {
        if (preg_match("/^AddDescription \"(.*)\" $bookname/u", $htaline, $matches)) {
            $descr = htmlentities($matches[1], ENT_QUOTES);
            if ($descr == $bookname)
                $title = $filename;
            else
                $title = "$filename: $descr";
            break;
        }
    }
    echo "<a href='$filename'><img style='$style' src='$image' title='$title' /></a>";
}
