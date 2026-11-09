'use strict';

/*jslint
    browser: true
*/
(function () {

    var fareTables = document.getElementById('fare-tables');
    var latestRequest = 0;
    if (fareTables && window.fetch) {
        fareTables.onchange = function(event) {
            if (event.target.value) {
                var container = document.getElementById('fare-table');
                container.className = 'loading';
                var request = ++latestRequest;
                fetch('/fares/tables/' + event.target.value).then(function(response) {
                    if (request !== latestRequest) {
                        return;
                    }
                    if (response.ok) {
                        response.text().then(function(data) {
                            container.innerHTML = data;
                            container.className = '';
                        });
                    } else {
                        container.className = '';
                    }
                });
            }
        };
    }

})();
