# How light aviation flies: measures on the same feed

**From 7 October 2026.** The Electronic Conspicuity Monitor measures how
visible light aircraft are to the networks that track them; that method is
[METHOD.md](METHOD.md). The same feed, recorded and read once a night by
the same code (METHOD.md, sections 9 and 10), also shows how light aviation
flies: at which hours each kind is in the air, how pilots circle, how
strong and how wide thermals are, how high each kind flies through the day,
how long flights last, how gliders are launched, where they climb in wave.
Those are different questions, for different readers, and they are
described here. Sections 1 and 2 were first published in METHOD.md as 10.1
and 10.2 (commit 7d04511, 7 October 2026) and moved here unchanged in
substance the same day.

The rules of METHOD.md apply throughout: the same sources and exclusions,
the owners' choices in the OGN device database, the time written in the
fix, the airborne speeds per kind (section 1), the implausible fixes, one
aircraft one 24-bit address, the day's majority category (section 10), and
ADS-B left out of every measure of sections 1 to 8 (sections 9 to 11
use it, for powered aircraft and helicopters). Only aggregates are kept and
published. As there, a rule is published before its data, and every later
change is dated here and in the log at the end.

The measures here are computed each night at 01:30 UTC by the same run as
those of METHOD.md (section 10 there), and written after them in a
transaction of their own, so a failure here never touches the conspicuity
figures, nor theirs these (from 7 October 2026).

## 1. When each kind of aircraft flies

The question: on which days and at which hours does each kind of aircraft
fly, which tells a regulator when the airspace is shared and with whom.
Hours are **local solar time**, UTC plus the longitude divided by 15 (one
hour every 15 degrees east), so that two in the afternoon is the same moment
of the day, with the sun at the same height, in Lisbon and in Bucharest.
Time zones and summer time would put an hour or two of difference between
places that fly at the same solar hours, and thermals follow the sun. The
weekday is that of the local solar date.

The flying time is that of METHOD.md section 1, one timeline per address, filed by the
solar hour at the middle of each airborne segment, at the longitude where
the segment began. Ground support and static objects are left out, and
drones (METHOD.md, section 10.1) are kept apart from crewed aircraft. Each hour also counts the
aircraft that flew in it; summed over hours that counts aircraft-hours, so
the number of aircraft is read hour by hour.

## 2. Circling direction

The question: do pilots circle more to one side, does each pilot keep a side
of their own, and do pilots who share a thermal turn the same way, which
matters to anyone predicting where a circling aircraft will be (METHOD.md,
section 6).

**Direction** comes from the change of course between successive fixes of
one device, so it does not need a turn rate. Only fixes at most 5 seconds
apart are used: in 15 seconds a paraglider circling once every 20 seconds
turns 270 degrees, which reads as 90 the other way. A change of course of 5
to 45 degrees a second (a full turn in 72 down to 8 seconds) is turning.
Right means clockwise seen from above; on the recording the turn rate FLARM
transmits agreed in sign with the change of course 99.4% of the time, FANET's
92%. Both fixes must be at the airborne speed for the kind (METHOD.md,
section 1), and
below 200 km/h.

**A thermal** is a run of turning to one side totalling at least **two full
turns** (720 degrees). One turn can be a manoeuvre; two in a row are a climb.
Runs to the same side within **10 minutes and 3 km** of each other are one
thermal, since pilots leave the core to recentre and packets drop out, and
both would otherwise split one climb into several. A thermal belongs to the
day of its first run. Measured for gliders, hang gliders and paragliders.

A correction of 8 October 2026: a circling episode, as above, is a
thermal only if **its largest climb gains at least 50 m**: the largest
rise from a low point to the highest point reached after that low point,
found by following the episode with a running minimum. Two turns alone also
caught a glider spiralling down to land and the turns of a pilot still
looking for the core, and both drew the climb down: on the first two
days published the cells of Schänis and Unterwössen, two alpine gliding
schools, averaged -1.02 and -0.98 m/s, with 53 of their 54 thermals
under 0.5 m/s. The same span gives the thermal its duration and its
climb rate (section 3), so the search turns before the low point and the
weak turns after the top fall outside it, while a turn in which the core
is lost and found again inside the climb stays in, as part of what that
climb cost. An episode that climbed and then ended lower than it started
keeps its climb: a spiral descent on the same side within 10 minutes and
3 km of a thermal merges into it (section 2), so measured from the
episode's lowest point, which then comes last, a real climb would be
lost. Every
measure that counts thermals uses only those that pass: the side of
sections 2 and 3, gaggles, places and the circling time of section 5. On
6 October 2026 the rule kept 745 of 1,020 glider thermals, 528 of 743
paraglider and 19 of 28 hang glider ones, and the mean climb rose from
0.64 to 1.15 m/s for gliders, 0.86 to 1.32 for paragliders and 0.59 to
1.05 for hang gliders; the share turned to the right moved by about a
point for gliders and paragliders. Of the thermals kept, 18 of gliders
and 12 of paragliders had climbed and then ended lower. Schänis kept 1 of
its 15 thermals that day and Unterwössen none of 14.

One aircraft is one address, and an instrument sending FLARM, FANET and ADS-L
under one address would count each thermal three times, so only the system
on which the address has most thermals that day is used. Per day and kind
the data keep the thermals, the degrees turned and the seconds circling, to
each side.

**Each pilot's side** is tested against chance. If every pilot of a kind
chose each side with the share p seen across all of them, the right-hand
share of a pilot with n thermals would vary around p with variance
p(1 − p)/n. The test compares the observed variance of the shares, among
pilots with at least 5 and at least 10 thermals, with that expected one, and
counts the pilots at 80% or more on one side against the number the binomial
distribution expects. It is computed when read, over the current and the
previous month, from each aircraft's thermals per day (METHOD.md, section
9); a pilot
needs many thermals, and one day gives few. Thermals of one pilot are not
independent of each other: a pilot joining a gaggle usually turns the way it
turns, which also widens the spread, so a wide spread is a sign of personal
preference without proving it.

**Gaggles.** Two aircraft share a thermal when, while both are circling,
they are within **500 m** of each other horizontally and **300 m**
vertically for at least **60 seconds** in all (positions compared every
5 seconds). Each such pair of thermals is counted once, as turning the same
way or opposite ways. If pilots chose sides independently, two of them would
agree with probability p² + (1 − p)², and for two kinds with shares pa and
pb, pa·pb + (1 − pa)(1 − pb); the page sets the observed share beside it.
Pilots in a thermal are expected to turn the way the first one turns, so the
gaggle figure says how far that holds, and how much of the side preference
above it may explain.

## 3. Thermals: radius, strength, and thermals shared by different kinds

The thermals are those of section 2, one system per aircraft, for gliders,
hang gliders and paragliders, each kind apart (paragliders and hang gliders
circle at different radii and speeds), with free flight also given as the
two together.

**Radius.** The radius of a circle is its circumference divided by 2π, and
over whole turns the circumference is the distance flown along the circles.
So the radius of a thermal is the distance flown while circling (the
ground speed at each fix times the time to the next, summed) divided by the
angle turned, in radians. Ground speed includes the wind: flying a circle in
a wind, the aircraft is faster over the ground on one side and slower on the
other by the same amount, so over whole turns the mean ground speed stays
close to the airspeed, off by roughly the square of the ratio of wind to
airspeed (about 3% with a wind of a third of the airspeed). A thermal has at least
two full turns, so the method uses whole thermals and never a single fix's
speed and turn rate, which the wind would bias by up to its own speed. The
radius is binned under 30, 30–50, 50–80, 80–120, 120–200 and over 200 m: a
paraglider circles at 30 to 50 m, a glider at 60 to 120.

**Strength.** The climb of a thermal is its largest climb (section 2):
the largest altitude gain from a position sampled while circling (every
5 seconds) to the highest one after it, divided by the time between the
two, when that time is at least 20 seconds; a thermal climbing over less
time gives no climb rate.
Until 8 October 2026 it was taken between the first and the last position
sampled, which made a spiral descent a thermal with a negative climb
(section 2). It is the average of the
thermal, in bands of under 0.5, 0.5–1, 1–1.5, 1.5–2, 2–3, 3–4 and over 4 m/s,
by kind, by local solar hour of the middle of the thermal (section 1), by
month, and by cell. From 7 October 2026 thermals are kept in cells of a
**quarter of a degree** (about 28 by 19 km in the Alps; 1 degree before),
and given both as 1-degree cells and as **thermal places**: for the current
and the previous month together, the busiest cells (most thermals) and the
strongest (highest mean climb), per kind, among the cells with at least 20
thermals in those two months, so that a cell never stands for a handful of
flights.

**Names.** A cell is named, for each kind, after **the site most of its
thermals were flown from** (from the evening of 7 October 2026): every
thermal is filed under the take-off of the flight it belongs to, matched to
the nearest site of the FIVL windgram service, else to the nearest
free-flight take-off in OpenStreetMap, within 2 km, for paragliders and
hang gliders, and to the nearest aerodrome in OpenStreetMap within 3 km for
gliders, and the cell takes the site with most thermals over the same two
months as the ranking. Where no flight's take-off matches a site, the cell
takes the site nearest its centre (for paragliders and hang gliders the
FIVL site, else the OpenStreetMap take-off; for gliders the aerodrome,
those tagged for gliding first, with its ICAO code in brackets when it has
one), among those inside the cell or within 5 km of its edge. The first
rule of the same evening used only the site nearest the centre, which named
the cell of Meduno and Monte Valinis after another FIVL site. The names are given as each
list gives them; FIVL's carry their province. A cell with no such site
takes the most populous town or village of at least 5,000 people inside
it, or the nearest within 25 km of its centre, from the GeoNames list of
places (cities5000), as "Bassano del Grappa (IT)"; a cell with neither is
shown by its coordinates. **A name labels the cell and is not a claim about
where any thermal in it was flown**: a cell is some 28 by 19 km, a site
names it because it is the nearest one to its centre, and the thermals may
have been flown from another site, or over open country. The sources are
credited in the README of the repository. The altitude is what each system reports (METHOD.md,
section 1); an offset cancels in a difference, so the climb rate does not
depend on the reference.

**Thermals shared by different kinds.** Two aircraft of different kinds
share a thermal by the gaggle rule of section 2 (within 500 m and 300 m for
60 seconds while both circle). Each such pair is counted by kind pair, a
glider with a paraglider for instance, and by the median vertical
separation while they were together: under 50, 50–100, 100–200 and
200–300 m. A glider and a paraglider in one thermal circle at different
speeds and radii, and how close in height they stay is what the separation
shows.

## 4. Height above the ground through the day

Airborne time by kind and local solar hour, in bands of height above the
terrain model (METHOD.md, section 3): 0–50, 50–120, 120–300, 300–600,
600–1,200, 1,200–2,000 and over 2,000 m. The 120 m edge is the ceiling of
the open category of drones; 300 m is where mobile coverage is described as
reliable. The kinds are those of section 1, with paragliders and hang
gliders apart (and free flight as the two together), and drones as kinds of
their own, confirmed and uncertain apart (METHOD.md, section 10.1). Each airborne
segment is filed at the height and the hour of its first fix. The shares
read as where in the sky each kind is at each hour of the day.

## 5. Circling and gliding

The share of airborne time spent circling in thermals (section 2) by kind
and local solar hour, for gliders, paragliders and hang gliders (and free
flight as the two together): the seconds circling over
the airborne seconds, in the same hour, of the aircraft that flew at least
one thermal that day. A thermal is seen only in fixes at most 5 seconds
apart (section 2), so an aircraft heard only on a system that sends less
often (FANET, most phone apps) never circles in the data however much it
climbs; over all airborne time free flight circled 1.6% of the time on
6 October 2026, which says more about its systems than about its flying.
The share is therefore that of the aircraft whose circling can be seen, and
an aircraft heard part of the day on a slower system still lowers it. Climbing in thermals and
gliding between them are the two halves of a cross-country flight, and the
share says how much of the day goes to each; it also says how much of the
time the turn rate matters most to anyone predicting a position (METHOD.md,
section 6).

## 6. Flights

A flight is the airborne time of one aircraft from the time it is first
heard airborne to the time it lands or is lost for good. Unlike the
20-minute session of METHOD.md section 1, which stays as it is for flying
time, a flight goes on across a silence of any length up to **2 hours**
when the aircraft was airborne on both sides, more than **150 m** above the
ground on both sides, and the distance between the two sides could be
flown in the time at the fastest its kind flies (70 km/h for a paraglider,
120 for a hang glider, 280 for a glider, 350 for a powered aircraft, 900
for a jet, 300 for a helicopter, 150 for a drone): a glider out of coverage
for half an hour behind a ridge is one flight. A silence that ends or
begins near the ground (a landing), a fix of the aircraft standing on the
ground (at most 3 kt, within 30 m of the terrain model) after its last
airborne one (a stop on the field, a relaunch on the hill), a silence
longer than 2 hours, or a distance too long for the time ends the flight.
Slow flight low over a ridge does not end it: a paraglider soaring into the
wind can hold almost still over the ground without landing. The standing-still rule applies to gliders, free flight and
fixed-wing powered aircraft only; a helicopter or a drone hovering low
looks the same as one that has landed, so theirs end only on a silence. The path across a silence is counted as the straight line
between its two sides, so path lengths are a lower bound.

For each flight: its duration (under 10 minutes, 10–30, 30–60, 1–2 h,
2–4 h, 4–8 h, over 8 h), its largest distance from where it began (under 1,
1–5, 5–20, 20–50, 50–100, 100–300, over 300 km), the length of its path
(under 5, 5–20, 20–50, 50–100, 100–300, 300–500, over 500 km), and the
local solar hour of its first airborne fix, by kind, with paragliders and
hang gliders apart (and free flight as the two together), and by the terrain
class of section 12 at its start.

**A flight counts only if it lasts at least 2 minutes**, for every kind
(from 7 October 2026). On 6 October 2026, 493 glider flights whose start was
not seen lasted a median of 6 seconds: an aircraft heard for a fix or two
at the edge of coverage, which is no flight anybody flew. A launch whose
flight is such a fragment, or which never made a flight at all, is not
counted either (section 7). Glider flights also
carry the launch method of section 7 (aerotow, winch, aerotow or
self-launch, self-launch, other, start not seen), from the launch found
within 5 minutes before to 10 minutes after the flight began. A flight belongs to
the day it began. An aircraft first heard in the air makes a flight whose
start was not seen; its duration and distances are of the part seen.

**Classes of flight** (from 7 October 2026, written before any of their
data existed). A flight's landing is **seen** when a fix of it standing on
the ground ends it (above). A flight that ends in silence is taken as
landed at its last fix, an **inferred** landing, when that fix is less than
**100 m** above the ground and lower than the aircraft was 30 to 60 seconds
before: for a paraglider, a hang glider or a glider, a low and descending
last position followed by silence is almost always a landing, the pilot
switching off or the instrument losing the last receivers behind the
ground. A powered aircraft also needs an aerodrome within **3 km** of that
last fix, since a powered aircraft low and descending elsewhere may simply
have flown out of coverage. Seen and inferred landings are counted apart,
so that the share of inferred ones stays visible. A flight with neither is
**end unseen**. Aircraft heard only by ADS-B are never seen standing: the
recording leaves out ADS-B reports from the ground (METHOD.md, section 9),
so their landings are inferred at best.

For **paragliders, hang gliders and gliders**, the **glide range** is how
far the aircraft could glide from its take-off without climbing: the
take-off altitude minus the landing altitude, times a typical glide ratio
of the kind (8 for a paraglider, 12 for a hang glider, 35 for a glider),
plus a quarter for the lift found on the way. A glider's take-off altitude
is its release height, or the top of its winch launch, above the ground at
the launch site when a tow or a winch launch was found (section 7), else the
altitude of its first airborne fix, like every flight of the other kinds.
A flight that never went beyond the glide range from its take-off is
**local**; one that went beyond it and landed within it is **out and
return**; one that landed beyond it is **cross-country**.

Two corrections of the evening of 7 October 2026, before publication. A
flight that **lands within 1 km of its take-off is back home** and never
cross-country, whatever its glide range: local or out and return as
above. And a flight that **starts on the ground with no launch found**
(every paraglider and hang glider, and a glider with no tow or winch
launch seen) takes as its take-off altitude **the top of its first climb**,
the highest it reached before coming down 50 m from it; when a thermal
follows the launch without a break, its top counts, which overestimates
the glide range, the cautious side for this purpose. Without either rule,
a flight from a flat field or a top-landing slope started and ended at the
same altitude, so its glide range was zero and any landing a few hundred
metres away read as cross-country: on 6 October 2026, 355 of 496 glider
cross-country flights, 190 of 343 paraglider and 25 of 41 hang glider
ones had landed within 1 km of their take-off, and out-and-return flights
were inflated the same way. A **paraglider** is called cross-country only
when it lands **more than 5 km** from its take-off: from a hill a first
climb of 200-300 m gives a glide range of 2-3 km, and on the 11-14 UTC dry
run of 6 October 34 of 91 paraglider cross-country flights had landed
1-5 km away, drifting down a valley more often than flying a route. A flight without
an altitude at either end is counted as such. For local and out-and-return
flights the length is the largest distance from the take-off; for
cross-country flights the straight line from take-off to landing; the path
length is given for all. Both by kind and by the terrain class of section
12. The glide range is a yardstick for sorting flights and makes no claim
about how each was flown: a paraglider can climb and return without ever
leaving its glide range, and a glider can land out within it.

For **powered fixed-wing aircraft** (tow planes, powered aircraft and jets)
the take-off and the landing are matched to the nearest OpenStreetMap
aerodrome within **3 km**. A flight that landed where it took off is
**local** if it never went more than **25 km** from it, **out and return**
otherwise; one that landed at another aerodrome is **one way**; one whose
take-off or landing matches no aerodrome is counted as **no airfield**. The
take-off is the first airborne fix, a kilometre or two from the runway.

**Routes.** The one-way flights of powered aircraft are counted per month
by unordered pair of aerodromes (named as OpenStreetMap names them, with
the ICAO code when present), with the flights and the distinct aircraft.
A route is shown by name only when at least **5 distinct aircraft** flew it
that month, and the rest are summed as "other routes": a route flown by
one or two aircraft tells one person's movements. To count distinct
aircraft the route of each address is kept for the current and the
previous month, as other address lists are (METHOD.md, section 9), and
then reduced to counts.

## 7. Launches

The question: how gliders and hang gliders get into the air, by aerotow, by
winch or otherwise, and for aerotows at what height the tug lets them go,
which says how much of the airspace near a gliding site a tow occupies.
Counted from 7 October 2026, from the fixes of METHOD.md section 10.2 (one aircraft one
address, its category the day's majority).

**Aerotow.** A pair flying together by the rule of METHOD.md section 10.2
(within 300 m at a
relative speed under 20 km/h for at least 60 seconds) of a glider or a hang
glider with a tow plane or powered aircraft, which begins with the towed
aircraft less than **100 m** above the terrain model: two aircraft that meet
in formation aloft are not a launch. A helicopter is never taken as a tug.
The **release** is where flying together ends, by a separation over 300 m
or a relative speed over 20 km/h, at the towed aircraft's height above the
ground then, in bands of under 300, 300–450, 450–600, 600–900 and over
900 m: a typical release is between 400 and 600 m, and the bands are drawn to
show where. Tows are counted per day and towed kind with the release band,
the duration (under 3, 3–5, 5–8, 8–12, over 12 minutes) and the 1-degree
cell where they began; tugs only as a distribution of how many towed 1,
2 to 5, 6 to 10 or more than 10 times that day, never by address.

**Winch.** A glider that goes from under **50 m** above the ground to at
least **200 m** within **60 seconds**, gaining at least 150 m of altitude
above sea level in that time at an average of at least **4 m/s**, with
every fix above 50 m in that climb at **70 to 150 km/h**, the speed of a
glider on the cable, and with no
aircraft flying together with it at the time (a steep aerotow would
otherwise pass). The altitude gain is required because height above the
ground also grows when a glider flies off a ridge or out over a valley: the
first count, without it, found 1,016 winch launches on 6 October 2026
against 308 aerotows. The climb rate is required because an aerotow whose
tug is not heard, and a self-launching glider, climb 150 m in 90 seconds
too, at 2 to 3 m/s; a cable launch climbs at 10 m/s or more. With 90
seconds and no climb rate the second count still found 996. The top of the launch is the greatest height in the 150
seconds after it began, in bands of under 300, 300–400, 400–500, 500–700 and
over 700 m.

**Where a launch is seen at all.** A glider or hang glider first heard
airborne after more than 20 minutes unheard counts as a launch only when
that first airborne fix is within **150 m** of the ground: a winch launch's
first seconds, an aerotow's first minute, a paraglider just off its hill,
and well above the error of the terrain model. One first heard higher up
was joined in the air, coming back into coverage or into the feed after a
gap, and is counted apart, as a **start not seen**, out of the launch split.
The first count, without this rule, gave 2,153 glider launches on 6 October
2026 against 1,745 glider flights: reappearances counted as launches.

A start, whatever its method, counts only when the flight it begins lasts
at least 2 minutes (section 6); a start heard for a few seconds and lost is
a fragment, not a launch. Aerotows and winch launches found by their own
rules are counted as found, since a tow and a cable climb are evidence of a
launch in themselves.

**No tow seen.** A launch seen within 150 m of the ground with neither an
aerotow nor a winch launch found from 5 minutes before to 10 minutes after.
For hang gliders the class stays as it is: it mixes foot launches from a
slope, aerotows whose tug is not heard and launches flown outside coverage,
and says nothing about any one of them.

**A glider launch read by its climb** (from the evening of 7 October 2026).
For a glider, the climb over the first **150 m** gained after the launch
decides:

- **6 m/s or more**: a winch launch the winch rule missed, mostly because
  the aircraft came into coverage partway through the launch. Nothing else
  a glider does climbs that fast that low; an aerotow climbs at 2 to 4 m/s.
- **1 to 5 m/s, straight (turning under 3 degrees a second on average) at
  90 to 150 km/h**: an **aerotow or a self-launch, not distinguishable**.
  The tug may not be heard (it carries nothing the network hears, or is
  just out of coverage), and a self-launching motorglider climbs at the same
  rates and speeds. It counts as a **self-launch** only with independent
  evidence: the OGN device database names the aircraft as a self-launching
  motorglider (Stemme, Arcus M, DG-808, DG-400, ASH 26 E, ASH 31 Mi,
  ASG 32 Mi, Antares, Taurus, Silent, Dimona, Falke, SF 25, Sinus),
  honouring the owner's choice not to be identified. The list is short on
  purpose: a model name also used by pure gliders would take them in, and a
  self-launcher missing from it stays "aerotow or self-launch".
- **Anything else**, including a launch that has not gained 150 m within
  10 minutes: **other**.

What the rules miss: a tow whose two aircraft are not both heard, a winch
launch whose fixes are too sparse to show the climb, and a tug or glider
whose category is wrong. The terrain model's error near an airfield is
small, since airfields are flat, but a hill site can be off by tens of
metres.

## 8. Probably wave

Mountain wave lets a glider climb in smooth air, often in a straight line
or in figure eights along the wave, high above the ground. A glider
**probably flies in wave** when it climbs at least **300 m** at an average
of at least **1 m/s** for at least **3 minutes**, starting above **2,500 m**,
while turning on average at most **3 degrees a second**, and less than one
full turn net over the climb. A thermal turns at 5 degrees a second or more
(section 2), so the turning rule keeps thermals out, and the net turn lets
figure eights in. 2,500 m keeps out most ridge and thermal climbs over
lowland and hills. A climb of the same glider counts once in half an hour.
Climbs are counted per day and 1-degree cell.

Ridge lift and convergence lines can look the same: a glider flying along a
ridge or a convergence climbs in straight lines too, and above high ground
2,500 m is not much. That is why these are labelled "probably wave" and
never wave, and why only the count per cell is published.

## 9. Cruise speed and altitude

For powered aircraft (tow planes, powered aircraft and jets) and
helicopters, by the day's majority category, every system included,
ADS-B among them. A **level segment** is a stretch of at least
**2 minutes** in which the altitude stays within **150 ft** of where it
began, with no silence over 30 seconds, beginning at least 300 m above the
ground: an aircraft in cruise. The level segments, by kind
(powered aircraft, helicopters), by ADS-B emitter category where the
address has one (A1 light aircraft, B4 ultralight, A7 rotorcraft, others
together, none without ADS-B) and by altitude reference: the time and
segments in bands of mean ground speed (under 100, 100–150, 150–200,
200–250, 250–300, over 300 km/h) and of altitude (under 3,000 ft, then
every 2,000 ft to 11,000, 11,000–15,000, over 15,000). The altitude is that
of each reference: the pressure altitude ADS-B transponders send (on the
standard 1013 hPa) and the altitude every system sends (from GPS), published
apart and never added together, since the two differ by the day's pressure
and by the geoid. Ground speed is not
airspeed: a tailwind adds to it and a headwind takes from it, so the bands
are of speed over the ground.

## 10. Helicopters by day and by night

Helicopter flying time (section 1 of METHOD.md, the airborne timeline) at
night, when the sun is more than **6 degrees below the horizon** (between
civil dusk and civil dawn), and by day, computed for each segment from its
position and time, by height above the ground (the bands of section 4) and
1-degree cell. A cell is published only with at least 10 helicopter-days in
the month. Night flights are mostly rescue, police and medical transport,
which the feed cannot tell apart from the rest.

## 11. Tow planes

From the aerotows of section 7: how many tows each tug flew that day (1,
2–5, 6–10, more than 10) and the share of its flying time spent towing (the
durations of its tows over its airborne time that day: under 25%, 25–50%,
50–75%, over 75%), as counts of tugs, never by address.

## 12. Plain and mountains

Thermals (section 3), height above the ground by hour (section 4),
circling and gliding (section 5), flights (section 6) and launches
(section 7) are also given by **terrain class**: **plain and hills** where
the highest minus the lowest ground within **5 km** is under **600 m**,
**mountains** from 600 m, from the terrain model of METHOD.md section 3.
Thermals take the class of the point where they end, flights and launches
of where they begin, airborne time of each segment's first fix.

The threshold was chosen on 7 October 2026 after looking at the relief
around every flight start and thermal of one October day, 6 October 2026,
in steps of 100 m. The medians were about 100 m for the starts of powered
aircraft, 200 m for gliders and helicopters, 900 m for hang gliders and
1,000 m for paragliders; around thermals, 300 m for gliders, 900 m for
paragliders and 1,000 m for hang gliders. The share under 600 m was 89% of
glider starts, 88% of powered, 73% of helicopter, 35% of hang glider and 21%
of paraglider starts; 80% of glider thermals and 19% of paraglider ones.
Between 500 and 700 m every kind had a dip (glider starts: 106 between 400
and 500 m, 15 and 21 in the two steps from 500 to 700, 46 and 75 at 800 to
1,000), which is where the line was drawn. That day included the fragments
section 6 now leaves out. **The threshold is to be checked again against a
summer month**, when the Alps fly differently and lowland thermals are
stronger.

The class describes the ground within 5 km of the aircraft, nothing more.
It does not say which lift was flown: a glider in the foothills can climb
in a thermal over flat ground 4 km from a 700 m slope and be filed under
mountains, and a pilot ridge soaring a 400 m hill is under plain and hills.
Nor does it follow regions: a valley floor in the Alps a few kilometres
wide sits in mountains, a high plateau in plain and hills.

## Limits

- Everything here is what the OGN network hears. An aircraft that carries
  nothing, or flies where no receiver hears it, is missing from every
  figure, and an aircraft heard only part of the day contributes only that
  part (METHOD.md, section 11).
- Measures built on circling (sections 2, 3 and 5) need fixes at most
  5 seconds apart, so they rest on the systems that send that often, FLARM,
  OGN trackers, SafeSky, and leave out the time of aircraft heard only on
  slower ones.
- Heights above the ground come from a terrain model with cells of a few
  hundred metres (METHOD.md, section 3): near a ridge they can be off by
  tens of metres, which matters for the lowest bands of section 4 and for
  launches (section 7).
- **Cruising levels are not measured (dropped on 7 October 2026, before
  publication).** A measure of the semicircular rule was written the same
  day. Below the transition altitude pilots set the local QNH, which the feed
  does not carry; a GPS altitude and a flight level on 1013 hPa both differ
  from it by hundreds of feet on a given day. On 6 October 2026 the rule
  looked followed by 17 to 24% of level segments, against 20% that chance
  alone gives with a ±200 ft window every 2,000 ft, so the figure said
  nothing about pilots. It may come back with the QNH of the nearest METAR.
- **Circuits and touch-and-go are not measured (dropped on 7 October 2026,
  before publication).** On 6 October 2026 the rule written that day found
  2,878 circuits and 2,816 touch-and-go for 3,290 powered flights, almost
  certainly tug cycles, and heights above the terrain model and reported
  altitudes off by more than the 20 m the rule allowed near the ground. The
  figures were not published.
- **Flying below minimum heights is not measured, on purpose.** A
  low-flying aircraft may be taking off, landing, ridge soaring, towing a
  banner, on a rescue or on police work, or infringing the rules, and the
  feed cannot tell these apart. A figure would read as a count of
  infringements and could not be one, so none is computed.

## Log of changes

- **7 October 2026.** File created from METHOD.md sections 10.1 and 10.2
  (commit 7d04511) and 10.7, with sections 3 to 6 and 8 added before any of
  their data existed. Winch launches require an altitude gain of 150 m at
  4 m/s within 60 s, after two first counts the same day that took aerotows
  and self-launches for winches (section 7). The share of time circling is
  taken over the aircraft seen circling (section 5). Later the same day,
  before publication: a launch must be first heard within 150 m of the
  ground, the rest counted as starts not seen (section 7); flights go on
  across silences the aircraft could have flown through, up to 2 hours
  (section 6); thermals of paragliders and hang gliders are given apart
  (section 3). In the evening, before publication: glider launches with
  no tow or winch found are read by their climb, and glider flights carry
  their launch method (sections 6 and 7); thermal cells of 0.25 degree
  and named thermal places (section 3); paragliders and hang gliders
  are given apart in sections 4, 5 and 6 as well. Later the same evening:
  the terrain class (section 12), and flights of at least 2 minutes, with
  the launches of shorter ones left out (sections 6 and 7). Then thermal
  places named by the take-off site of the flights that circled in them
  (section 3), and classes of flight and routes (section 6). Sections 9 to 11, on powered aircraft
  and helicopters, added before any of their data existed; ADS-B is used
  in them, unlike in the sections above, for the pressure altitude and the
  emitter category. Circuits and touch-and-go, and cruising levels, written
  the same day, were dropped before publication (Limits). The expected histogram of
  the per-pilot test (section 2) is computed pilot by pilot.
- **8 October 2026.** A thermal must gain at least 50 m in its largest
  climb, from a low point to the highest point after it, and its
  duration and climb rate are taken over that span (sections 2 and 3).
