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
ADS-B left out of every measure here. Only aggregates are kept and
published. As there, a rule is published before its data, and every later
change is dated here and in the log at the end.

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
hang gliders and paragliders.

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

**Strength.** The climb of a thermal is the altitude gained between the
first and last position sampled while circling (every 5 seconds) divided by
the time between them, when that time is at least 20 seconds; a thermal
sampled over less time gives no climb rate. It is the average of the
thermal, in bands of under 0.5, 0.5–1, 1–1.5, 1.5–2, 2–3, 3–4 and over 4 m/s,
by kind, by local solar hour of the middle of the thermal (section 1), by
month, and by 1-degree cell. A cell is published only where at least 20
thermals were flown in the month, so that a cell never stands for a
handful of flights. The altitude is what each system reports (METHOD.md,
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
reliable. The kinds are those of section 1, with drones as kinds of their
own, confirmed and uncertain apart (METHOD.md, section 10.1). Each airborne
segment is filed at the height and the hour of its first fix. The shares
read as where in the sky each kind is at each hour of the day.

## 5. Circling and gliding

The share of airborne time spent circling in thermals (section 2) by kind
and local solar hour, for gliders and free flight: the seconds circling over
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

A flight is the airborne time of one address between silences or ground
stops longer than 20 minutes, the session of METHOD.md section 1. For each:
its duration (under 10 minutes, 10–30, 30–60, 1–2 h, 2–4 h, 4–8 h, over
8 h), its largest distance from where it began (under 1, 1–5, 5–20, 20–50,
50–100, 100–300, over 300 km), the length of its path (under 5, 5–20, 20–50,
50–100, 100–300, 300–500, over 500 km), and the local solar hour of its
first airborne fix, by kind. A flight belongs to the day it began. An
aircraft that leaves coverage for more than 20 minutes makes two flights,
and a pilot who lands and takes off again within 20 minutes makes one: the
counts are of what the network sees, which for free flight in the mountains
is shorter than what was flown.

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

**No tow seen.** A flight of a glider or hang glider, begun when it is first
heard airborne after more than 20 minutes unheard, with neither an aerotow
nor a winch launch found from 5 minutes before to 10 minutes after. The
class mixes several things and says nothing about any one of them:
self-launching and motor gliders, bungee and foot launches, hang gliders
launched from a slope, launches flown outside coverage, and aircraft that
come into coverage already aloft. It is published for completeness and is
not a count of any launch method.

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
  taken over the aircraft seen circling (section 5). The expected histogram of
  the per-pilot test (section 2) is computed pilot by pilot.
