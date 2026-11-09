'use strict';

/*jslint
    browser: true
*/

(function() {
    if (!window.fetch) {
        return;
    }

    var pathMatch = window.location.pathname.match(/^\/(stops|stations)\/([^/]+)/);
    if (!pathMatch) {
        return;
    }
    var basePath = '/' + pathMatch[1] + '/' + pathMatch[2];

    var search = window.location.search,
        now,
        latestRequest = 0;

    function updateDepartures(event) {
        var newSearch;

        if (this instanceof HTMLFormElement) {
            var formData = new FormData(this);
            newSearch = '?' + new URLSearchParams(formData).toString();
        } else if (event.type === 'popstate') {
            newSearch = window.location.search;
        } else {
            newSearch = event.target.search;
        }

        if (newSearch === now) {
            newSearch = search;  // search may be ''
        }

        var departures = document.getElementById('departures');

        departures.classList.add('loading');

        var request = ++latestRequest;

        fetch(basePath + '/departures' + newSearch).then(function(response) {
            if (request !== latestRequest) {
                return;
            }
            if (response.ok) {
                response.text().then(function(text) {
                    function update() {
                        departures.outerHTML = text;

                        search = newSearch;

                        setUp();

                        document.getElementById('departures').scrollIntoView();
                    }

                    if (document.startViewTransition) {
                        document.startViewTransition(update);
                    } else {
                        update();
                    }

                    if (window.location.search !== newSearch) {
                        if (newSearch) {
                            history.pushState(null, "", newSearch);
                        } else {
                            history.pushState(null, "", basePath);
                        }
                    }
                });
            } else {
                departures.classList.remove('loading');
            }
        });
        return false;
    }

    function setUp() {
        var departures = document.getElementById('departures');
        var form = departures.querySelector('form');
        var submitButton = form.querySelector('input[type=submit]')

        form.addEventListener("input", function() {
            if (submitButton.value !== "Go") {
                submitButton.value = "Go";
                submitButton.title = "";
                submitButton.ariaLabel = null;
            }
        })

        var formData = new FormData(form);

        now = '?' + new URLSearchParams(formData).toString();

        form.onsubmit = updateDepartures;

        // 'Now <' / 'Later >' links
        departures.querySelectorAll('p a').forEach(function(a) {
            a.onclick = updateDepartures;
        });
    }

    setUp();

    window.addEventListener('popstate', updateDepartures);

}());
