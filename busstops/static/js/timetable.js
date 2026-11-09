'use strict';

/*jslint
    browser: true
*/
/*global
    SERVICE_ID
*/

(function() {
    var timetableWrapper = document.getElementById('timetable');

    var latestRequest = 0;

    function getHTML(url, callback) {
        var request = ++latestRequest;
        fetch(url).then(function(response) {
            if (request !== latestRequest) {
                return;
            }
            if (response.ok) {
                response.text().then(callback);
            } else {
                timetableWrapper.className = '';
            }
        });
    }

    function doStuff() {
        if (document.querySelectorAll && document.referrer.indexOf('/stops/') > -1) {
            var referrer = document.referrer.split('/stops/')[1];
            referrer = referrer.split('?')[0];
            if (referrer) {
                // highlight the row of the referring stop
                document.querySelectorAll('tr th a[href="/stops/' + referrer + '"]').forEach(function(a) {
                    a.parentNode.parentNode.className += ' referrer';
                });
            }
        }

        // load timetable for a particular date without loading the rest of the page
        if (timetableWrapper) {
            var timetableForm = timetableWrapper.querySelector('form');
        }
        if (timetableForm) {
            timetableForm.onchange = function() {
                if (!window.fetch || typeof URLSearchParams === "undefined" || typeof FormData === "undefined") { // old browser
                    timetableForm.submit();
                    return;
                }

                timetableWrapper.className = 'loading';

                var formData = new FormData(timetableForm);
                var newSearch = '?' + new URLSearchParams(formData).toString();
                getHTML('/services/' + SERVICE_ID + '/timetable' + newSearch, function(response) {
                    function update() {
                        timetableWrapper.className = '';
                        timetableWrapper.innerHTML = response;
                        doStuff();
                    }

                    if (document.startViewTransition) {
                        document.startViewTransition(update);
                    } else {
                        update();
                    }

                    search = newSearch;
                    history.pushState(null, "", newSearch);
                });
            };
        }

    }

    doStuff();

    var search  = window.location.search;

    window.addEventListener('popstate', function() {
        // handle back/forward navigation
        if (search !== window.location.search) {
            search = window.location.search;
            var url = '/services/' + SERVICE_ID + '/timetable';
            if (search) {
                url += search;
            }
            timetableWrapper.className = 'loading';
            getHTML(url, function(response) {
                function update() {
                    timetableWrapper.className = '';
                    timetableWrapper.innerHTML = response;
                    doStuff();
                }

                if (document.startViewTransition) {
                    document.startViewTransition(update);
                } else {
                    update();
                }
            });
        }
    });
}());
