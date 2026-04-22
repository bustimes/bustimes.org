# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/jclgoodwin/bustimes.org/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                            |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| accounts/\_\_init\_\_.py                                        |        0 |        0 |    100% |           |
| accounts/admin.py                                               |       47 |        0 |    100% |           |
| accounts/forms.py                                               |       52 |        0 |    100% |           |
| accounts/models.py                                              |       32 |        0 |    100% |           |
| accounts/tests.py                                               |      112 |        0 |    100% |           |
| accounts/urls.py                                                |        4 |        0 |    100% |           |
| accounts/views.py                                               |       61 |        1 |     98% |       102 |
| api/\_\_init\_\_.py                                             |        0 |        0 |    100% |           |
| api/api.py                                                      |       11 |        0 |    100% |           |
| api/filters.py                                                  |       70 |        0 |    100% |           |
| api/serializers.py                                              |      120 |        5 |     96% |225-228, 230 |
| api/tests.py                                                    |        9 |        0 |    100% |           |
| api/views.py                                                    |       97 |        1 |     99% |       146 |
| buses/\_\_init\_\_.py                                           |        0 |        0 |    100% |           |
| buses/asgi.py                                                   |        4 |        0 |    100% |           |
| buses/context\_processors.py                                    |        5 |        0 |    100% |           |
| buses/jinja2.py                                                 |       36 |        1 |     97% |        40 |
| buses/settings.py                                               |      122 |       33 |     73% |97-107, 127, 141-149, 218, 237-245, 262-280, 340 |
| buses/tests.py                                                  |       12 |        0 |    100% |           |
| buses/urls.py                                                   |        5 |        0 |    100% |           |
| buses/utils.py                                                  |       11 |        4 |     64% |     13-16 |
| buses/wsgi.py                                                   |        4 |        0 |    100% |           |
| busstops/\_\_init\_\_.py                                        |        0 |        0 |    100% |           |
| busstops/admin.py                                               |      328 |       17 |     95% |105-111, 185, 244-252, 334-336, 339-341, 387, 462, 466, 516, 601, 629 |
| busstops/fields.py                                              |       27 |        0 |    100% |           |
| busstops/forms.py                                               |       46 |        0 |    100% |           |
| busstops/management/\_\_init\_\_.py                             |        0 |        0 |    100% |           |
| busstops/management/commands/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| busstops/management/commands/import\_noc.py                     |      131 |        4 |     97% |156, 177, 184, 246 |
| busstops/management/commands/import\_tfl.py                     |       67 |       67 |      0% |     1-127 |
| busstops/management/commands/jersey\_routes.py                  |       14 |        0 |    100% |           |
| busstops/management/commands/jersey\_stops.py                   |       12 |        0 |    100% |           |
| busstops/management/commands/lothian\_colours.py                |       17 |        1 |     94% |        19 |
| busstops/management/commands/naptan\_new.py                     |      160 |        3 |     98% |41, 43, 270 |
| busstops/management/commands/nptg\_new.py                       |       96 |        9 |     91% |142, 155, 160, 165-166, 172-173, 175-176 |
| busstops/management/commands/osm\_iom\_stops.py                 |       34 |       34 |      0% |      1-63 |
| busstops/management/commands/update\_search\_indexes.py         |       11 |        0 |    100% |           |
| busstops/management/commands/update\_slugs.py                   |       20 |       20 |      0% |      1-29 |
| busstops/management/tests/\_\_init\_\_.py                       |        0 |        0 |    100% |           |
| busstops/management/tests/test\_import\_ie.py                   |       63 |        0 |    100% |           |
| busstops/management/tests/test\_import\_naptan.py               |       73 |        0 |    100% |           |
| busstops/management/tests/test\_import\_nptg.py                 |       31 |        0 |    100% |           |
| busstops/management/tests/test\_import\_operators.py            |       70 |        0 |    100% |           |
| busstops/middleware.py                                          |       31 |        0 |    100% |           |
| busstops/models.py                                              |      629 |       22 |     97% |273, 298, 324, 448, 485, 535, 619, 622, 656, 694, 844, 877, 956, 968-972, 1054, 1063, 1103, 1118, 1155 |
| busstops/popular\_pages.py                                      |       12 |        0 |    100% |           |
| busstops/tasks.py                                               |       11 |        0 |    100% |           |
| busstops/templatetags/\_\_init\_\_.py                           |        0 |        0 |    100% |           |
| busstops/templatetags/date\_range.py                            |       25 |        3 |     88% | 9, 22, 28 |
| busstops/templatetags/test\_urlise.py                           |        8 |        0 |    100% |           |
| busstops/templatetags/urlise.py                                 |       18 |        0 |    100% |           |
| busstops/test\_admin.py                                         |       69 |        0 |    100% |           |
| busstops/test\_middleware.py                                    |       25 |        0 |    100% |           |
| busstops/test\_models.py                                        |      137 |        0 |    100% |           |
| busstops/test\_popular\_pages.py                                |       21 |        0 |    100% |           |
| busstops/test\_views.py                                         |      295 |        0 |    100% |           |
| busstops/urls.py                                                |       19 |        2 |     89% |   134-136 |
| busstops/utils.py                                               |       11 |        0 |    100% |           |
| busstops/views.py                                               |      675 |       49 |     93% |101, 147, 594, 620, 658, 731-733, 735, 823, 841, 1018, 1038-1043, 1056, 1135, 1163-1164, 1169-1170, 1178-1187, 1249, 1346, 1362-1369, 1376, 1378, 1456, 1512, 1550-1551, 1554-1558, 1708, 1720-1721, 1726, 1738-1739 |
| bustimes/\_\_init\_\_.py                                        |        0 |        0 |    100% |           |
| bustimes/admin.py                                               |      142 |       14 |     90% |92, 95-96, 107-109, 112-114, 171-175, 198, 219, 222 |
| bustimes/download\_utils.py                                     |       32 |        0 |    100% |           |
| bustimes/fields.py                                              |       31 |        1 |     97% |        12 |
| bustimes/formatting.py                                          |       23 |        0 |    100% |           |
| bustimes/gtfs\_utils.py                                         |       63 |        1 |     98% |        89 |
| bustimes/management/\_\_init\_\_.py                             |        0 |        0 |    100% |           |
| bustimes/management/commands/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| bustimes/management/commands/bank\_holidays.py                  |       52 |        1 |     98% |       102 |
| bustimes/management/commands/import\_atco\_cif.py               |      235 |       25 |     89% |28-35, 40, 43-44, 71, 204, 206, 209, 211, 219, 225, 289, 322-327, 367-368, 435 |
| bustimes/management/commands/import\_bod\_timetables.py         |      264 |       28 |     89% |35-43, 63, 106, 110, 113-116, 121, 126-127, 151, 179, 189, 200-201, 268, 285-289, 312, 328-329, 350 |
| bustimes/management/commands/import\_gtfs.py                    |      193 |        6 |     97% |67, 120-121, 133, 140, 340 |
| bustimes/management/commands/import\_gtfs\_ember.py             |      154 |        5 |     97% |43, 106, 226, 258, 271 |
| bustimes/management/commands/import\_gtfs\_flixbus.py           |      138 |        3 |     98% |43, 108, 190 |
| bustimes/management/commands/import\_ni.py                      |       29 |        0 |    100% |           |
| bustimes/management/commands/import\_passenger.py               |      116 |       35 |     70% |51-54, 56-58, 64, 103, 113-117, 143-180 |
| bustimes/management/commands/import\_tnds.py                    |       42 |        0 |    100% |           |
| bustimes/management/commands/import\_transxchange.py            |      833 |       88 |     89% |90, 105, 151-152, 235-236, 247, 327-328, 338, 346-347, 402, 406, 408, 510, 569-583, 603, 607, 610, 722, 764-765, 802-803, 805-806, 850, 861, 885, 891, 915, 941, 945-947, 996, 1003-1007, 1012-1032, 1060, 1069, 1077-1082, 1098, 1122, 1130, 1132-1135, 1140, 1156, 1186, 1225, 1265-1266, 1294, 1299-1300, 1315, 1320, 1339, 1364, 1390-1391, 1435-1436, 1485, 1498-1499 |
| bustimes/management/commands/suggest\_bod.py                    |       36 |       36 |      0% |      1-51 |
| bustimes/management/tests/\_\_init\_\_.py                       |        0 |        0 |    100% |           |
| bustimes/management/tests/test\_bank\_holidays.py               |       20 |        0 |    100% |           |
| bustimes/management/tests/test\_import\_atco\_cif.py            |       70 |        0 |    100% |           |
| bustimes/management/tests/test\_import\_bod.py                  |      245 |        0 |    100% |           |
| bustimes/management/tests/test\_import\_gtfs.py                 |      105 |        0 |    100% |           |
| bustimes/management/tests/test\_import\_gtfs\_ember\_flixbus.py |       80 |        0 |    100% |           |
| bustimes/management/tests/test\_import\_ni.py                   |       21 |        0 |    100% |           |
| bustimes/management/tests/test\_import\_passenger.py            |       23 |        0 |    100% |           |
| bustimes/management/tests/test\_import\_transxchange.py         |      783 |        0 |    100% |           |
| bustimes/management/tests/test\_tnds.py                         |       19 |        0 |    100% |           |
| bustimes/models.py                                              |      347 |        7 |     98% |126, 232, 439, 484-487, 530 |
| bustimes/test\_get\_trip.py                                     |       31 |        0 |    100% |           |
| bustimes/tests.py                                               |      134 |        0 |    100% |           |
| bustimes/timetables.py                                          |      646 |       50 |     92% |39-52, 162-173, 204-205, 293, 307-312, 325, 344, 419, 422, 438-441, 443, 470, 582-597, 748-749, 820, 966-968, 1017 |
| bustimes/urls.py                                                |        3 |        0 |    100% |           |
| bustimes/utils.py                                               |      207 |       13 |     94% |175, 243, 272, 281-283, 304-305, 372, 390, 407-408, 482 |
| bustimes/views.py                                               |      452 |      111 |     75% |137-240, 312-313, 316-317, 333-335, 343-352, 354-362, 369, 391, 480, 489, 525, 531, 722, 746-748, 765, 769-797, 827-838, 847-853, 895 |
| departures/\_\_init\_\_.py                                      |        0 |        0 |    100% |           |
| departures/avl.py                                               |       12 |        1 |     92% |        14 |
| departures/gtfsr.py                                             |       91 |        4 |     96% |91, 111, 117-120 |
| departures/live.py                                              |      129 |       18 |     86% |42, 57, 59, 61, 72-83, 144, 155, 167, 181 |
| departures/sources.py                                           |      194 |       35 |     82% |30, 54, 59, 63, 67, 71, 92, 104, 107-109, 119-126, 131-132, 146-147, 155-156, 251, 268-270, 343, 347-348, 354-355, 358 |
| departures/test\_gtfsr\_trip\_updates.py                        |       50 |        0 |    100% |           |
| departures/test\_gtfsr\_vehicle\_positions.py                   |       33 |        0 |    100% |           |
| departures/test\_live.py                                        |       96 |        0 |    100% |           |
| departures/test\_scheduled\_departures.py                       |       18 |        0 |    100% |           |
| disruptions/\_\_init\_\_.py                                     |        0 |        0 |    100% |           |
| disruptions/admin.py                                            |       33 |        1 |     97% |        69 |
| disruptions/models.py                                           |       97 |        6 |     94% |52, 83, 103, 123, 133, 161 |
| disruptions/siri\_sx.py                                         |      124 |       14 |     89% |54, 74, 86-89, 100-101, 132, 142-146 |
| disruptions/tasks.py                                            |        5 |        0 |    100% |           |
| disruptions/test\_siri\_sx.py                                   |       48 |        0 |    100% |           |
| disruptions/test\_tfl\_disruptions.py                           |       34 |        0 |    100% |           |
| disruptions/test\_translinkni.py                                |       25 |        0 |    100% |           |
| disruptions/tests.py                                            |       20 |        0 |    100% |           |
| disruptions/tfl\_disruptions.py                                 |       91 |        2 |     98% |    49, 85 |
| disruptions/translinkni.py                                      |       80 |        7 |     91% |45, 56-59, 68, 110 |
| disruptions/urls.py                                             |        3 |        0 |    100% |           |
| disruptions/views.py                                            |       23 |        0 |    100% |           |
| fares/\_\_init\_\_.py                                           |        0 |        0 |    100% |           |
| fares/admin.py                                                  |       38 |        1 |     97% |        32 |
| fares/forms.py                                                  |       26 |        3 |     88% | 18, 41-42 |
| fares/management/commands/\_\_init\_\_.py                       |        0 |        0 |    100% |           |
| fares/management/commands/import\_netex\_fares.py               |      353 |       59 |     83% |28, 69-70, 121, 128-130, 223-224, 323, 367-368, 449, 528-535, 555, 564-565, 581-586, 593-630, 650-651, 661-662, 668-677 |
| fares/management/commands/mytrip\_ticketing.py                  |       34 |        0 |    100% |           |
| fares/models.py                                                 |      190 |        8 |     96% |60, 142, 217, 222, 245-246, 250-251 |
| fares/mytrip.py                                                 |       50 |        2 |     96% |     47-48 |
| fares/test\_mytrip.py                                           |       47 |        0 |    100% |           |
| fares/tests.py                                                  |       79 |        0 |    100% |           |
| fares/urls.py                                                   |        3 |        0 |    100% |           |
| fares/views.py                                                  |       45 |        0 |    100% |           |
| manage.py                                                       |        6 |        0 |    100% |           |
| photos/\_\_init\_\_.py                                          |        0 |        0 |    100% |           |
| photos/admin.py                                                 |        5 |        0 |    100% |           |
| photos/forms.py                                                 |        3 |        0 |    100% |           |
| photos/models.py                                                |       19 |        1 |     95% |        38 |
| photos/utils.py                                                 |       25 |       18 |     28% |10-12, 16-43 |
| vehicles/\_\_init\_\_.py                                        |        0 |        0 |    100% |           |
| vehicles/admin.py                                               |      258 |       14 |     95% |34, 194-195, 293, 299, 301, 303, 306, 351, 357, 461, 521-523 |
| vehicles/apps.py                                                |        6 |        0 |    100% |           |
| vehicles/context\_processors.py                                 |       14 |        0 |    100% |           |
| vehicles/fields.py                                              |       23 |        0 |    100% |           |
| vehicles/filters.py                                             |       20 |        0 |    100% |           |
| vehicles/form\_fields.py                                        |       15 |        0 |    100% |           |
| vehicles/forms.py                                               |       94 |        3 |     97% |129, 167, 205 |
| vehicles/management/\_\_init\_\_.py                             |        0 |        0 |    100% |           |
| vehicles/management/commands/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| vehicles/management/commands/compute\_blocks.py                 |       27 |       27 |      0% |      1-45 |
| vehicles/management/commands/import\_bod\_avl.py                |      418 |       59 |     86% |43-45, 145, 161, 171-172, 192, 219-222, 225-228, 271, 287, 302-303, 325-343, 394, 396, 402, 425-426, 433, 439, 455, 497-510, 535-536, 542, 549-550, 565, 627, 716, 733 |
| vehicles/management/commands/import\_bushub.py                  |       83 |       18 |     78% |25-26, 30-31, 34-35, 39, 43, 51, 68, 83-84, 92, 102, 120, 128, 135, 153 |
| vehicles/management/commands/import\_first.py                   |      123 |       21 |     83% |27, 31, 51, 53, 74-75, 81, 103-104, 113, 135-147, 152, 211-212, 214 |
| vehicles/management/commands/import\_gtfsr\_ember.py            |       50 |        0 |    100% |           |
| vehicles/management/commands/import\_gtfsr\_ie.py               |       89 |        4 |     96% |94, 108, 139, 146 |
| vehicles/management/commands/import\_live\_jersey.py            |       39 |        0 |    100% |           |
| vehicles/management/commands/import\_stagecoach\_avl.py         |       72 |        7 |     90% |119, 129, 134-136, 168-169 |
| vehicles/management/commands/import\_translink\_avl.py          |       58 |        8 |     86% |13, 66-69, 85, 107-108 |
| vehicles/management/commands/listen.py                          |       24 |        0 |    100% |           |
| vehicles/management/commands/lothian.py                         |       55 |        0 |    100% |           |
| vehicles/management/commands/newport.py                         |       44 |        2 |     95% |    46, 62 |
| vehicles/management/commands/signalr.py                         |       82 |        0 |    100% |           |
| vehicles/management/commands/siri\_vm\_subscribe.py             |       43 |        2 |     95% |    41, 49 |
| vehicles/management/import\_live\_vehicles.py                   |      309 |       34 |     89% |85, 98-116, 128, 134, 140-142, 144, 172, 181, 195, 204-207, 237, 242, 251, 256, 313-314, 355, 358-359, 380-381, 483, 521-522, 532, 552 |
| vehicles/management/tests/\_\_init\_\_.py                       |        0 |        0 |    100% |           |
| vehicles/management/tests/test\_bod\_avl.py                     |      323 |        0 |    100% |           |
| vehicles/management/tests/test\_bushub.py                       |       48 |        0 |    100% |           |
| vehicles/management/tests/test\_edinburgh.py                    |       46 |        0 |    100% |           |
| vehicles/management/tests/test\_first.py                        |       33 |        0 |    100% |           |
| vehicles/management/tests/test\_import\_live\_jersey.py         |       46 |        0 |    100% |           |
| vehicles/management/tests/test\_listen.py                       |       15 |        0 |    100% |           |
| vehicles/management/tests/test\_newport.py                      |       23 |        0 |    100% |           |
| vehicles/management/tests/test\_signalr.py                      |       28 |        0 |    100% |           |
| vehicles/management/tests/test\_siri\_post.py                   |       50 |        0 |    100% |           |
| vehicles/management/tests/test\_stagecoach\_avl.py              |       34 |        0 |    100% |           |
| vehicles/management/tests/test\_stats.py                        |       24 |        0 |    100% |           |
| vehicles/management/tests/test\_translink\_avl.py               |       22 |        0 |    100% |           |
| vehicles/models.py                                              |      517 |       35 |     93% |75, 193, 218, 285, 304, 310, 382, 388, 439, 460, 581-582, 595, 603, 620-622, 630-631, 634-635, 637-642, 647-648, 679, 760-763, 818, 827, 853 |
| vehicles/rtpi.py                                                |       92 |        6 |     93% |35, 84-85, 91-93 |
| vehicles/signals.py                                             |       11 |        0 |    100% |           |
| vehicles/tasks.py                                               |      163 |       36 |     78% |97, 102, 113, 120-123, 126, 143, 156, 160-161, 183, 208, 216-217, 225, 288-289, 292, 313-333 |
| vehicles/test\_models.py                                        |       62 |        0 |    100% |           |
| vehicles/test\_schedule\_adherence.py                           |       75 |        0 |    100% |           |
| vehicles/tests.py                                               |      481 |        0 |    100% |           |
| vehicles/time\_aware\_polyline.py                               |       56 |       28 |     50% |18, 36, 49, 54, 57, 87-98, 106-120 |
| vehicles/urls.py                                                |        4 |        0 |    100% |           |
| vehicles/utils.py                                               |      128 |        6 |     95% |29, 59, 162, 174-175, 179 |
| vehicles/views.py                                               |      653 |       69 |     89% |459-460, 479-480, 493, 511-512, 564, 580-581, 586, 602-603, 640-641, 655-657, 666, 674, 696-701, 735, 749, 757-760, 766-774, 786, 893, 895, 897, 902, 952-953, 965-967, 1055, 1058-1059, 1069, 1137-1139, 1163-1164, 1173-1180, 1194-1204, 1208, 1210, 1234 |
| vosa/\_\_init\_\_.py                                            |        0 |        0 |    100% |           |
| vosa/admin.py                                                   |       37 |        0 |    100% |           |
| vosa/management/commands/\_\_init\_\_.py                        |        0 |        0 |    100% |           |
| vosa/management/commands/import\_vosa.py                        |      162 |        3 |     98% |25-26, 206 |
| vosa/models.py                                                  |       75 |        0 |    100% |           |
| vosa/tests.py                                                   |       60 |        0 |    100% |           |
| vosa/urls.py                                                    |        3 |        0 |    100% |           |
| vosa/views.py                                                   |       59 |        0 |    100% |           |
| **TOTAL**                                                       | **16974** | **1262** | **93%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/jclgoodwin/bustimes.org/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/jclgoodwin/bustimes.org/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/jclgoodwin/bustimes.org/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/jclgoodwin/bustimes.org/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fjclgoodwin%2Fbustimes.org%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/jclgoodwin/bustimes.org/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.