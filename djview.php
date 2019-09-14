<?php
ini_set('memory_limit','300M');
header("Content-Type: text/html; charset=utf-8");
$file = $_GET['file'];
if (isset($file) && $file != '') {
   echo "<html>";
   echo "<head>";
   echo "<link rel='stylesheet' type='text/css' href='/b/urantia-library/djvu/Djvu_html5.css'>";
   echo "</head>";
   echo "<body>";
   echo "<div id='djvuContainer' file='".$file."'></div>";
   echo "<script src='/b/urantia-library/djvu/djvu_html5/djvu_html5.nocache.js'></script>";
   echo "</body>";
   echo "</html>";
   flush();
   exit;
}
?>
