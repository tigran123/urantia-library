$('.buttons').button();

$('#search').click(function(event) {
   var html = $('#search_text').val().trim(); /* may contain html tags */
   var text = $('<div/>').html(html).text(); /* strip html tags, if any */
   if (!text || text.length == 0) {
      $('#search_text').focus();
      return;
   }
   $('#search_text').addClass('loading').prop('disabled', true);
   $('#search').button('disable');
   var search_req = "/b/urantia-library/search.php?text=" + encodeURIComponent(text);
   $.ajax({url: search_req, dataType: 'json', success: function(data) {
      var json = JSON.parse(data);
      $('#search_results').html(json.matches);
      $('#search_text').removeClass('loading').prop('disabled', false);
      $('#search').button('enable');
      $('#search_text').focus();
   }, dataType: "html"});
});

$(document).keydown(function(event) {
   var key = event.which, ctrl = event.ctrlKey;
   if (key == 13 && event.target.id == 'search_text') { /* ENTER in a search input box */
      event.preventDefault();
      $('#search').click();
   } else if (ctrl && key == 88) { /* Ctrl + X, clear search field */
      event.preventDefault();
      $('#search_results').html('');
      $('#search_text').val('').focus();
   }
});
