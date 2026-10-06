# How electronic conspicuity is measured

**Version 1, in force from 4 October 2026, 00:00 UTC.** Every figure published
from that moment on follows the rules below. They were worked out and tested
on the live feed during 3 October 2026; the data of that day served to test
them and was discarded. Any later change to a rule is committed to this file
with its date and its reason, and the figures it affects say so. The
development history of version 1 was not published.

## 1. The data

**Source.** The public APRS feed of the Open Glider Network (OGN), which
carries what its volunteer ground receivers hear by radio and what apps and
platforms forward to it over the internet.

**Sources.** Each packet names its source in the APRS destination field (the
"tocall", listed in `tocalls.txt` in glidernet/ogn-aprs-protocol): FLARM,
ADS-L, FANET, OGN trackers, PilotAware, ADS-B, and each phone app or platform.
Position reports from the receivers themselves, delayed copies and synthetic
packets are left out. Some systems appear under more than one tocall, for
protocol versions or decoders: FLARM as OGFLR, OGNFLR, OGFLR6 and OGFLR7,
PilotAware as OGPAW and OGNPAW, and as the generic APRS when PilotAware's own
ground stations forward its devices (their prefix PAW identifies them; until
09:51 UTC on 5 October 2026 they were stored under other radio, and the
counts reassign those rows when they are computed, so the October 2026
figures include them). Each is treated as one source, so a device
heard under two of them is followed as one stream and counted once. Until
15:19 UTC on 4 October 2026 they were followed apart, which split one
FLARM into two sparser streams that looked less visible than the device was
and counted its flying time twice; the October 2026 figures mix the two rules
up to that time.

Three kinds of packet are left out because they describe no aircraft. FANET
forwards the reports of its weather stations and the beacons of its ground
stations under the same source as its aircraft. A position within 1 degree of
latitude and longitude of 0,0 comes from a device without a fix, or from a
FLARM packet decoded by a receiver that does not know where it is: FLARM sends
only the low part of the position and the receiver supplies the rest from its
own. Weather reports are left out from 08:20 UTC on 5 October 2026, the other
two from 08:51 UTC. Before that they were counted as FANET devices of unknown
category, and a weather station counted as flying whenever the wind it reported
reached 10 km/h. The October 2026 device counts were cleaned of them
afterwards.

Meshtastic, a mesh network for people on the ground that some receivers
decode, counts only for nodes whose id declares an aircraft type, from 10:22
UTC on 5 October 2026. Earlier rows of nodes that declare none are left out
whenever the counts are computed, so the October 2026 figures exclude them.

**Channel.** A packet is **radio** when it carries the reception figures that
a ground receiver adds (signal-to-noise in dB and frequency offset in kHz),
and **internet** otherwise. ADS-B is radio by definition, although its
receivers add no such figures. A source not yet listed is still assigned a
channel this way. The same device heard both ways is followed separately on
each channel.

**Privacy.** The OGN device database records each owner's choices, and the
OGN data usage terms require every service to respect them: a device whose
owner asked not to be tracked is dropped on arrival and appears nowhere, in
the map, the counts or the measures; one whose owner asked not to be
identified is shown and counted without its aircraft model or registration.
Devices are matched to the database by their 24-bit address. How long a
device address is kept is set out in section 9.

**Aircraft category** comes from the aircraft type in the packet's OGN id
when there is one, and from the APRS symbol otherwise (`g` paraglider or hang
glider, `'` glider, `^` powered aircraft, `X` helicopter, `O` balloon). Naviter writes a longer id of its own, with the aircraft type in other bits;
until 09:21 UTC on 5 October 2026 it was not read, and since Naviter sends the
glider symbol for every aircraft, its paragliders were counted as gliders in
the measures and without a category in the device counts.

**Do not track.** A packet whose id carries the owner's no-tracking flag is
dropped on arrival, like a device marked in the OGN device database, from
09:21 UTC on 5 October 2026.

**Time** is the instant of the fix written in the packet. A packet whose fix
is more than 5 minutes older than its arrival is ignored, since it would open
a gap that never happened, and so is a packet older than the last one received
from the same device. An app that regains coverage and sends its stored
positions in a burst therefore counts as invisible for the minutes it was
silent, which is intended: nobody could see the aircraft at the time.

**Airborne.** Two consecutive positions of a device on a channel form a
segment. A segment counts as airborne when the ground speed at both ends is
at least 15 km/h for paragliders and hang gliders, 25 kt for gliders and
40 kt for powered aircraft; for any other kind, when it is at least 10 km/h
at either end. Until 11:00 UTC on 6 October 2026 the 10 km/h rule applied to every kind,
and the raw feed replayed under both rules showed what it cost: a powered
aircraft taxiing and a pilot packing up after landing were flying, and the
silence of a phone app on the ground counted as lost signal. Below 300 m
above the ground, apps on powered aircraft were without signal 33% of the
time under the old rule and 4% under the new one, while above 300 m the
figures barely moved. A paraglider soaring a ridge in a strong wind can fall
below 15 km/h over the ground and lose that stretch; a threshold of 10 km/h
at both ends kept most of the time on the ground and recovered little flight.

**New session.** A segment longer than 20 minutes is treated as a new session
(the device was switched off, or the pilot drove to another site) by the
measures of sections 2, 3 and 5. Section 4 counts it as a disappearance.

**Implausible segment.** A segment implying more than 500 km/h is excluded:
some addresses are shared by several devices at once, and followed as one
device they produce jumps of hundreds of kilometres. From 11:00 UTC on
6 October 2026 a
position of a paraglider faster than 100 km/h, of a hang glider faster than
150 km/h, or of either above 6,000 m is excluded too: the feed carried a
"paraglider" at 8,150 m and 157 km/h, most likely a sounding balloon with a
tracker set to that category, and another reporting 702 km/h.

**ADS-B** is counted among the sources but left out of the measures of
visibility, being almost entirely airliners.

**Flying time** of each kind of aircraft is measured once per aircraft. Every
fix of the same 24-bit address joins one timeline, whichever source and
channel it came by, and the airborne segments of that timeline are added up
with the rules above. A paraglider whose instrument sends FLARM, FANET and
ADS-L under one address counts its time once, and so does a pilot whose phone
app uses the address of the aircraft's own device. Two devices with different
addresses on one aircraft still count twice, and an aircraft nobody hears
counts nothing. The measure starts at 15:19 UTC on 4 October 2026, and for
aircraft of unknown category at 08:33 UTC on 5 October 2026, when the time the
FANET weather stations had added to that category was removed.

**Systems per aircraft.** One aircraft is one 24-bit address, and its
systems are the sources it was heard by in the month, on either channel:
FANET by radio and through an internet gateway is one system. Systems are
grouped as radio, phone app or tracker, and platforms that only relay other
sources are left out. An instrument that sends several protocols under one
address therefore counts as one aircraft on several systems, and so does a
phone app set up with the address of the radio device on board, which SafeSky
asks its users to do. Two devices with different addresses on one aircraft
count as two aircraft. Combinations shared by fewer than 5 aircraft are
pooled. These counts need the device addresses, so they exist for the months
whose addresses are still kept (section 9).

## 2. Visibility through the network

The question: for how much of its flight does somebody watching through a
network see an aircraft more than a given distance from where it really is?
Watching through a network means through ground receivers or the internet,
which is how U-space services, drones, rescue teams and tracking maps see
aircraft. Between two aircraft close to each other, two radios hear each
other directly even where no ground receiver does, and a phone does not;
that case is outside what this feed can show.

**Distance between positions.** A gap is judged by how far the aircraft
moved. Thirty silent seconds leave a paraglider 300 m from its last point and
a glider at 150 km/h more than a kilometre and a quarter. The thresholds are
**300 m**, the distance by which a device that sends a position every 150 m
falls behind when one transmission is lost, and roughly where a paraglider
becomes hard to find by eye, and **1 km** and
**3 km**, for the tail and for fast aircraft, which cover 300 m in a few
seconds.

**Three levels.** The position shown is **up to date** while it is within
300 m of the aircraft, **approximate** between 300 m and 1 km, which still
tells anyone watching that an aircraft is about there, and **lost** beyond
1 km: more than a minute and a half of silence for a paraglider at 36 km/h,
15 seconds for an aircraft at 240 km/h. The page leads with the share of
time lost and gives the share up to date beside it.

**Estimate.** The published figures use the **last point**: the position a
plain map shows, where the last packet put the aircraft. It is the same
yardstick for every source, and it depends only on how far the aircraft moved
before its next packet arrived. Two more estimates are kept in the data: one
moving the aircraft on along its last course at its last speed, or holding it
in place when that packet reported circling; and the same with the turn rate
ignored. The value of course, speed and turn rate is measured properly in
section 6.

**Measure.** For a segment of T seconds, the error E is the distance between
the estimate at the end of the segment and the position the next packet
reports. Within the segment the error is taken to grow linearly from zero to
E, so the time beyond a threshold D is T × (1 − D/E) when E exceeds D. The
figure is **the share of airborne time during which the position shown was
more than D away**, summed over the segments of a group (source, channel,
aircraft category, height band, cell). For an aircraft circling in a thermal
the true error oscillates rather than grows, so this overstates the gap a
little; the choice is deliberately the cautious one.

**Time without signal, for comparing channels (from 5 October 2026).** The
distance measure above mixes three things: the coverage of the channel, how
often each source chooses to send, and the speed of the aircraft. Some apps
send a position once a minute by design, and in that minute a glider covers
two kilometres whatever the network does. How far behind an aircraft falls
therefore depends on how often each app chooses to send, which is a property
of the app. Speed weighs in the same way: 1 km is a minute and a half of
silence for a paraglider at 36 km/h and 15 seconds for an aircraft at
240 km/h, and gliders and powered aircraft fly faster high above the ground
than in the circuit, so a distance threshold also changes with height.

To compare the channels themselves, the comparison between radio and phone
apps (question 2 of the page), the losses by height above the ground
(section 3, question 3) and the map of where aircraft are lost (question 6)
count only the **time without signal**: for each segment of T seconds, the
part beyond the interval the source keeps by design at the slower end's
speed, plus a tolerance of 10 seconds, the same rule as section 8. How often
a source should send is a separate question, answered by the measure in
seconds and metres of section 5 (question 4 of the page).

The intervals kept by design are those of section 8 for the apps, and for
radio: **FLARM and ADS-L 1 second**, **OGN trackers and PilotAware
2 seconds**, **FANET 15 seconds**. FANET's specification sets its interval
by the number of FANET devices in range, floor((neighbours / 10 + 1) × 5 s):
5 seconds with fewer than ten, 10 with ten to nineteen, 15 with twenty to
twenty-nine, so that a busy site does not saturate the channel. On
6 October 2026 the feed showed it: with fewer than ten FANET devices within
10 km the commonest interval in flight was 5 seconds, with ten to nineteen
the median was 17. Counting from the network the devices a FANET instrument
hears would undercount them, since the network does not hear every device,
so the method takes 15 seconds, the interval up to 29 neighbours. For an
isolated pilot it is lenient by up to 10 seconds a silence. Until 11:00 UTC
on 6 October 2026 FANET was judged by 5 seconds, which counted its slowing down
at busy sites as lost signal. With the 10-second tolerance, a radio counts as
without signal after a silence of 11 to 25 seconds. Sources whose interval is not
known, radio or app, are left out of this measure; ADS-B stays out as
everywhere else. The rule applies from 17:51 UTC on 5 October 2026, and the
October 2026 figures for these questions count only the time since then.
The airborne rule and FANET's interval changed at 11:00 UTC on 6 October
2026. The hours from 06:07:56 UTC that day, when the raw feed began to be
recorded (section 9), were computed again from the recording under the new
rules and the difference added, for positions in Europe, the only ones
recorded. The October 2026 figures therefore include the hours from 17:51 UTC
on 5 October to 06:07:56 UTC on 6 October, and outside Europe to 11:00 UTC,
under the earlier rules.
The distance measure stays in the data and on the page for each source,
where it describes what a map shows, cadence included.

## 3. Where and how high

Every segment is attributed to the place and height where the aircraft was
last seen before it, which is where coverage gave way:

- **Height above sea level**, in bands of 1,000 m, up to "above 4,000 m".
- **Height above the ground**, in bands of 0–300, 300–600, 600–1,200,
  1,200–2,000 and above 2,000 m. Mobile coverage is expected to weaken with
  height above the ground, because cell antennas are tilted downwards, and in
  the mountains that height and the altitude differ by thousands of metres.
  The boundaries follow the claims the data should test: mobile operators
  describe coverage as reliable up to 300 m and patchy up to about 1,000 m,
  and the 2021 feasibility study for EASA recorded a permanent loss of the
  tracking link between 600 and 1,200 m above the ground. The ground is the
  NOAA ETOPO 2022 surface model at 15 arc-seconds (cells of about 460 by 310 m
  at the latitude of the Alps) over 35–72°N and 25°W–45°E; over the sea it is
  the sea surface. The ground under an aircraft is interpolated between the
  centres of the four nearest cells (bilinear), which follows a slope across
  a cell; the value of the one cell containing the point would treat the
  slope as flat. Tested on the Alps by averaging the model to cells twice as
  large and reading it back at 400,000 random points, interpolation lowered
  the error on the steepest tenth of the ground from a median of 100 m to
  30 m, and its 90th percentile from 220 m to 90 m. Relief smaller than a
  cell stays: close to a ridge the ground can still differ from the model by
  some tens of metres to over a hundred, which moves segments near a band
  boundary without changing a band's result. Outside that area the height
  above the ground is unknown.
- **Cell**, a square of 0.25 degrees (about 28 by 19 km in the Alps), for a
  map of where each channel loses aircraft.

## 4. Disappearances

The share of time of section 2 counts only segments that close, and its
denominator is the time aircraft were seen. An aircraft lost for good, or for
longer than a session, adds nothing to it. So every disappearance is counted
as well.

A device last seen airborne that falls silent counts one disappearance for
each threshold its silence exceeds, **2, 5 and 20 minutes**, at the height
above sea and above the ground of its last position: when it is heard again,
for the thresholds the gap exceeded; when it has been silent for 20 minutes
without being heard again, for all three. A paraglider silent for 2 minutes
has moved more than a kilometre at 36 km/h. Divided by the airborne hours at
the same height, this gives **disappearances per hour flown**. Near the ground
the figure includes landings; high above it nobody lands and nobody switches
an instrument off, so a disappearance there is almost always lost coverage,
and bands and channels can be compared without knowing how many aircraft were
up there unseen.

## 5. How often packets arrive, and seconds against metres

Every airborne segment is counted by its length, at most 2, 4, 8, 16, 32 and
64 seconds, by source, channel, category and height band: the real interval
between received packets.

The update rule this project argues for is a distance: a position is sent
whenever the aircraft has moved a set number of metres from the last one,
with a slow heartbeat for an aircraft that is not moving. Light aircraft such
as paragliders and hang gliders carry small batteries, often a phone's, and a
rule in metres spends transmissions only when the position has changed; and
the airspace is shared by aircraft whose speeds differ tenfold, so a fixed
interval leaves a fast aircraft far from its last point and makes a slow one
transmit for nothing.

Requirements discussed for U-space are stated in seconds (an update within 3
to 6 seconds with 95% probability). To show on the same flights where the two
readings disagree, the share of airborne time during which the last position
was no older than 3, 6, 15 and 30 seconds (min(T, X) of each segment) is kept
beside the distance measure. A paraglider at 30 km/h sending every 150 m
updates every 18 seconds and fails a 6-second requirement while its position
is never more than 150 m off; an aircraft at 250 km/h updating every 6
seconds passes it, after moving 400 m.

How far each kind of aircraft moves in a given silence is read from the
measures of section 6: the error of the fourth prediction, S itself, 10
seconds ahead in straight flight is the distance flown in those 10 seconds. Its
median per kind, and the seconds after which the position falls 300 m
behind (10 × 300 / that median), are shown with question 4.

## 6. The turn rate

**Where it comes from.** It is sent by the aircraft in FLARM's current
protocol, by the OGN tracker (from its own GPS headings), by FANET devices
that include the optional byte, and by VarioVoice. ADS-L has no such field: a
few receivers derive one from the previous packet, so the same packet appears
with a turn rate through one station and without it through another. FLARM's
old protocol, ADS-B, PilotAware and the generic APRS stream write zero or
nothing; Naviter does not document it. A turn rate is therefore used only
from the sources that send it, and for each device only from its first
non-zero value, since a device that only ever reports zero is not sending
one. It is read as half-turns per minute, positive to the right; on the live
feed the reported rate agreed in sign with the observed change of course 97%
of the time, at 2.95 degrees a second per unit against the 3 expected.

**Predicting a few seconds ahead.** The turn rate matters at the horizon of
collision avoidance, 5 to 20 seconds and tens of metres. For received
positions P (at most one every 3 seconds per device, to keep the cost on the
server low) and horizons of 5, 10 and 20 seconds, the earlier position S
closest to that horizon (within one second) is the start, if it reports a
course and at least 10 km/h. Four predictions of where the aircraft will be
at the time of P are scored by their distance from P:

- a straight line along S's course at S's speed;
- an arc with the turn rate a receiver derives from S and the position 2 to 6
  seconds before it;
- an arc with the turn rate S carries;
- S itself.

Results are kept by source, category, horizon, and circling (a turn rate of
at least 2 half-turns per minute, a full circle a minute) or straight flight,
as counts in error bins (5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 300,
500 m and over) with the sum of the errors, so that medians and means can be
read. The derived and the sent turn rate are compared only at the instants
where both exist. A prediction implying more than 500 km/h is excluded.

**When packets are lost.** A turn rate in the packet matters most when
packets are lost, as when a pilot's body shields the radio: the receiver then
holds two positions far apart in time, and over 8 seconds a paraglider in a
thermal turns a third of a circle, over 16 more than half, so the curvature
it can derive from them is wrong or ambiguous, while the packet that does get
through still carries the turn rate. This is measured on the same tracks by
emulating the loss: for gaps of 2, 4, 8, 16 and 32 seconds, the receiver is
assumed to have kept only the start and the position that long before it
(within half a second); the turn rate derived from that pair is compared with
the one the start carries, at the same instant. A gap of 32 seconds is about
the interval at which a phone app sends.

## 7. How an installation shields the signal

An aircraft circling in a thermal turns through a full circle every 20 to 30
seconds, so its transmitter faces a ground receiver from every side in turn,
equally often. If the installation radiated equally well all round, the
packets received would be spread evenly over the angle between the
aircraft's heading and the direction of the receiver; where a body or an
airframe shields the antenna, they go missing.

For every radio packet received while circling (a turn rate of at least 2
half-turns per minute or, without one, a change of course of at least 6
degrees a second since the previous packet, no more than 10 seconds earlier),
the angle is recorded in twelve sectors of 30 degrees, by source, category
and distance to the receiving station (0–5, 5–10, 10–20, 20–40, over 40 km),
whose position comes from its own reports in the feed. The sectors are centred
on the heading, the first from 15 degrees left of it to 15 degrees right, so
the nose, the wings and the tail each fall in the middle of one. Until
6 October 2026 the sectors began at the heading, the first from 0 to 30
degrees; those counts are kept apart and never added to the new ones, and the
page draws them, each at the centre of its sector, only until the new ones
are enough for a chart. The receiver's
signal-to-noise ratio, corrected for distance by the free-space loss (adding
20·log10 of the distance in kilometres), is summed per sector, so the shadow
can be read in decibels. While an aircraft circles, every receiver sees it
from every side equally often, so differences in receiver sensitivity cancel.

Stations close to the aircraft receive almost every packet whatever the
direction, which hides a shadow; it shows at the far stations, where the
signal is near the limit of reception. The packet pattern is therefore drawn
from stations more than 20 km away. The signal-to-noise ratio is read from
stations within 5 km: there few packets go missing, so the average is not
raised by the weak ones dropping out, as it is farther away. The two are
independent readings of the same shadow, from different stations and
different quantities.

What the shadow costs in range is derived from the second. The difference in
decibels between the strongest and the weakest sector becomes a share of the
range, 10^(−dB/20), on the assumption of two aircraft in line of sight,
where the signal falls with the square of the distance: every 6 dB halves
the range. Each sector needs at least 300 packets with a signal-to-noise
ratio before the figure is given.

The receiving stations are on the ground, below the aircraft, so both
readings concern the signal that leaves the transmitter downwards: a station
5 km away and 1,000 to 2,000 m lower is seen 10 to 20 degrees below the
horizon, one 20 to 40 km away 2 to 6 degrees below. While circling the
aircraft is also banked, by 20 to 45 degrees, so a station on the inside of
the turn lies well below the pilot's horizontal plane and one on the outside
above it. Towards another aircraft at the same height the shadow may be
stronger or weaker; no ground station can measure it.

Gliders and powered aircraft on the same radio system are the comparison,
and an imperfect one. FLARM's antenna note answers "Does the human body
attenuate the FLARM signal?" with "Yes", and the most common glider
installation sits on or above the instrument panel with the pilot right
behind it, while carbon-fibre fuselages shield further. In powered aircraft
the antenna may be on the panel, on the canopy or under a metal fuselage,
and circling is mostly the turns of a circuit, a smaller sample. The
comparison is between ways of carrying a transmitter: on a paraglider
pilot's harness or pod, mostly in front of a glider pilot, and in a powered
aircraft. The feed shows one receiving station per packet, the first to
forward it, and a phone app has no receiving station, so this measure
applies to radio only.

## 8. How much flying happens where phone apps work

Whether an app-based means of compliance is viable depends on where people
fly, above all where carriage cannot be enforced. For each month:

- A cell counts as **covered by apps** when phone apps logged at least 2 hours
  of airborne time in it and had signal for at least 95% of that time. Each
  app sends at its own pace: SafeSky every 2 seconds and Naviter every 60, as
  measured on the feed, and VarioVoice once the aircraft has moved 150 m, but
  never sooner than 10 seconds nor later than 45. For each interval between
  two fixes, only the part beyond the interval the app is expected to keep at
  the slower of the two speeds, plus 10 seconds, counts as time without
  signal. An app whose cadence is not known is left out of the judgement.
  This rule applies from 16:04 UTC on 5 October 2026, and the free-flight time
  of the figure is counted per aircraft (below) from 06:07:56 UTC on
  6 October 2026, the hours before 11:00 UTC computed again from the raw
  recording. Until 16:04 UTC on 5 October a cell was judged by
  whether the apps' last position stayed within 300 m of the true one, which
  mostly measured where Naviter is used, since a fix a minute leaves an
  aircraft behind on the map whatever the coverage.
- The airborne time of paragliders and hang gliders is counted per aircraft,
  whatever systems carry it and radio included, so that pilots who carry no
  app count too, and attributed to the cell where each segment began. From
  11:00 UTC on 6 October 2026 a pilot heard on two systems counts once; until then the
  time was summed over every source and channel, which counted such pilots
  twice and gave them twice the weight, and those figures are no longer used.
- The figure is the share of that time spent in covered cells, among the
  cells with at least 2 hours of app data. Counting the other cells as not
  covered gives a lower bound over all the time, published alongside. Both
  are published only when less than half of the time falls in cells with too
  little app data. Two devices of one pilot under different addresses still
  count twice, which a spot check on 5 October 2026 found rare.

## 9. What is kept

Monthly lists of device addresses per source and channel, for counting
devices; daily and monthly totals per source, channel, category, height band
and cell for the measures above; reception counts by angle; prediction errors
by bin. Apart from the raw feed kept for four days, described below, no track
and no position of any aircraft is stored.

A device address can be traced to an aircraft and its pilot, so the lists of
addresses are kept for the current and the previous month only. Once a month
is older than that, its lists are reduced to the counts the published figures
use (devices per month by address type, category, source and channel, and how
many were heard on two different days) and the addresses are deleted. The
previous month is kept whole so that the share of devices heard again from
one month to the next can be measured; a source whose ids change often shows
almost none. The service checks every six hours, so a month's addresses go
within a few hours of the end of the following month.

The plan was to keep nothing of the feed beyond the counts above, and the
first weeks showed what that costs. Several measures had to be redefined once
the data showed what they were really measuring. Question 2 first compared
sources by the distance an aircraft flew between two packets, which mixed in
its speed and judged each app by the interval its makers chose; on 5 October
2026 it became the time without signal beyond each source's own cadence. On
6 October the sectors of the reception pattern were turned by 15 degrees so
that the nose falls in the middle of one. Every such change restarted its
measure from zero, because counts already stored cannot be recomputed in a
new way, and a feed this varied (a dozen systems, each with its own cadence,
quirks and ways of failing) keeps producing cases that only real data
reveal.

From 6 October 2026 the service therefore keeps the feed it receives for four
days, so that a new or corrected algorithm can be run on real data before it
replaces the old one. It is a tool for the setting-up of the measures, and
its size on disk is the price of that. It is not designed to retain data, and
nothing older than four days exists.

The recording holds the lines of the OGN feed as they arrive, with their time
of reception, limited to positions inside 35–72° N and 25° W–45° E, together
with the receiving stations' own reports. ADS-B packets from airliners are
left out: those that declare a jet, fly above 15,000 ft or faster than
200 kt, or are reported on the ground, where an airliner taxis. So is every
packet from a device that asks not to be tracked, in its own id or in the
OGN device database. The files stay on the server,
readable by the service account alone; each hour is compressed once it is
over and deleted after four days. Only the aggregates of this method leave
the server, as with the live feed. The recording is a setting of the service and is meant to be switched
off once the measures have settled.

## 10. Known limits

- A device never heard at all does not appear, and a pilot who carries
  nothing is invisible to this measure as to everyone else.
- Radio counts stop where OGN receivers stop, and app counts include only the
  users who share their position and the apps that forward to OGN at all;
  neither is the number of pilots.
- Receiver coverage and mobile coverage are not spread the same way, so a
  comparison between radio and internet across different populations mixes
  the channel with the place and the pilot. The cleanest comparisons are the
  same device on both channels, the same pilot carrying two systems, and the
  same radio system on different aircraft.
- Random addresses, which change at power-up, can count one device several
  times; they are shown apart.
- Positions are only as good as the fix that produced them.
