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
heard under two of them is followed as one stream and counted once.
PilotAware's ground stations also rebroadcast the FLARM aircraft they hear,
under the PilotAware prefix with the FLARM device's address and the category
"ground support": in October 2026, 560 of the 1,463 PilotAware ids were such
copies. From 7 October 2026 they are not counted as PilotAware devices, and
the counts drop them whenever they are computed, so the October 2026 figures
exclude them too; the aircraft itself is counted under FLARM, its own system.
A genuine PilotAware ground vehicle, which declares the same category, is
lost with them, and is not an aircraft. Until
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
The measures take the category of each packet. The device counts take one
category per device, source and month, and until 7 October 2026 it was the
one of the device's first packet of the month. FANET instruments switch to
ground tracking once the pilot has landed and then declare a static object,
so a pilot first heard after a landing was counted as one: 3,070 of the 9,096
October FANET rows up to that day were static objects. From 7 October 2026 a
later aircraft category replaces a stored ground support or static object
category, also on a row written earlier in the month once the device is
heard again; rows of devices not heard again keep the category they had.
From the same day ADS-B's category 0 is treated the same way. OGN's ADS-B
decoder sends 0 until the aircraft's emitter category has been received, so
it means "not yet known" there, and the first packet of most ADS-B aircraft
carries it: of the October 2026 ADS-B rows also in the raw recording, 1,654
were stored as 0 while most of the aircraft's packets declared a powered
aircraft, a helicopter or a paraglider.
Drone Remote ID, decoded by some OGN receivers and forwarded under its own
source, is counted as a radio system from 7 October 2026, and its devices as
drones whatever their id declares.

**Do not track.** A packet whose id carries the owner's no-tracking flag is
dropped on arrival, like a device marked in the OGN device database, from
09:21 UTC on 5 October 2026.

**Time** is the instant of the fix written in the packet. A packet whose fix
is more than 5 minutes older than its arrival is ignored, since it would open
a gap that never happened, and so is a packet older than the last one received
from the same device. A fix stamped more than 5 minutes after its arrival is
read as belonging to the previous day, and is therefore ignored as stale. An
app that regains coverage and sends its stored
positions in a burst therefore counts as invisible for the minutes it was
silent, which is intended: nobody could see the aircraft at the time.

**Airborne.** Two consecutive positions of a device on a channel form a
segment. A segment counts as airborne when the ground speed at both ends is
at least 8 kt (14.8 km/h; APRS carries whole knots) for paragliders and hang
gliders, 25 kt for gliders and 40 kt for powered aircraft; for any other kind, when it is at least 10 km/h
at either end. Until 11:00 UTC on 6 October 2026 the 10 km/h rule applied to every kind,
and the raw feed replayed under both rules showed what it cost: a powered
aircraft taxiing and a pilot packing up after landing were flying, and the
silence of a phone app on the ground counted as lost signal. Below 300 m
above the ground, apps on powered aircraft were without signal 33% of the
time under the old rule and 4% under the new one, while above 300 m the
figures barely moved. A paraglider soaring a ridge in a strong wind can fall
below 15 km/h over the ground and lose that stretch; a threshold of 10 km/h
at both ends kept most of the time on the ground and recovered little flight.
From 11:00 UTC to 17:45 UTC on 6 October 2026 the free-flight threshold
was written as 15 km/h in knots, which only 9 kt (16.7 km/h) passed. The
measures that count flying time (flying time itself, the distances of section
2 and 5, the disappearances of section 4) hold the time before 06:07:56 UTC
on 6 October 2026 under the 10 km/h rule: the raw feed was not recorded then
(section 9), so it cannot be computed again.

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
visibility and of flying time, being almost entirely airliners.

**Flying time** of each kind of aircraft is measured once per aircraft. Every
fix of the same 24-bit address joins one timeline, whichever source and
channel it came by except ADS-B, and the airborne segments of that timeline are added up
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
sources are left out, as are aircraft heard by ADS-B alone, almost all
airliners (until 17:45 UTC on 6 October 2026 they were counted, and
10,820 of them sat among the powered aircraft of October). An instrument that sends several protocols under one
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
everywhere else. So are the packets of a radio system forwarded by an
internet gateway (FANET base stations, for one): this measure judges
reception by the radio network, and their timing is the gateway's. On
6 October 2026, between 09:00 and 13:00 UTC, they were 132 hours of free
flight on FANET.

The radio channel is followed per aircraft: all the radio packets of one
address, whatever system carries them, form one timeline, and a silence
counts beyond the longest interval among the systems heard from that address
in the previous 20 minutes, so only when every system is late on its own
interval: the per-system rule, applied to the aircraft. Until 17:45 UTC on
6 October 2026 the shortest was used, which judged an instrument's FANET
packets by FLARM's 1 second and counted FANET's designed interval as silence
again. Many
free-flight instruments alternate FLARM, FANET and ADS-L under one address,
and summed system by system such an aircraft would weigh two or three times.
On 6 October 2026, between 09:00 and 13:00 UTC, free flight by radio was
without signal 47% of the time system by system and 37% per aircraft: 32%
for aircraft sending two or more radio systems, 54% for those sending one,
almost all FANET alone. Four fifths of that time were silences of one to twenty
minutes, at every height. The figures per system stay in the data, and are
what each system achieves alone. Two devices of one pilot
under different addresses are still two aircraft. This applies from 13:17
UTC on 6 October 2026, the hours from 06:07:56 computed from the raw
recording.

The rule applies from 17:51 UTC on 5 October 2026, and the
airborne rule and FANET's interval changed at 11:00 UTC on 6 October 2026.
The figures for these questions count only the time judged under the current
rules, from 06:07:56 UTC on 6 October, when the raw feed began to be recorded
(section 9). The hours up to 12:16 UTC that day (13:17 for radio followed
per aircraft) were computed from the recording, for positions in Europe, the only ones recorded; from then the
service counts them itself. The time judged under the earlier rules, from
17:51 UTC on 5 October, stays in the database and is not used.
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
  and the 2021 feasibility study for EASA, a desk study by Horváth &
  Partners with no flights of its own, quoted trials by others (SafeSky's
  among them) that lost the tracking link between 600 and 1,200 m above the
  ground. Corrected on 8 October 2026: until then this section read as if
  the study had recorded the loss itself. The ground is the
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

For every radio packet received while circling from an aircraft in flight
(at the ground speed of the airborne rule for its kind, from 17:45 UTC on
6 October 2026, since an aircraft turning while it taxis is not circling; a
turn rate of at least 2
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
  This rule applies from 16:04 UTC on 5 October 2026. The figure counts the
  apps' time and the free-flight time (below) from 06:07:56 UTC on
  6 October 2026 under the rules of section 2, the hours before 12:16 UTC
  computed from the raw recording. Until 16:04 UTC on 5 October a cell was judged by
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
by bin; the daily totals of section 10 and of PATTERNS.md, and for each
aircraft that circled,
its thermals to each side per day. Apart from the raw feed kept for seven
days, described below, no track and no position of any aircraft is stored.

A device address can be traced to an aircraft and its pilot, so the lists of
addresses are kept for the current and the previous month only. Once a month
is older than that, its lists are reduced to the counts the published figures
use (devices per month by address type, category, source and channel, and how
many were heard on two different days) and the addresses are deleted. The
previous month is kept whole so that the share of devices heard again from
one month to the next can be measured; a source whose ids change often shows
almost none. The service checks every six hours, so a month's addresses go
within a few hours of the end of the following month.

Some published figures can only be computed from the addresses, and until
7 October 2026 they were lost with them. From that day each month is reduced,
before its addresses are deleted and in the same step, to what those figures
need as well:

- **Systems per aircraft** (section 1), as published: aircraft on one, two,
  and three or more systems per category, those with a radio and a phone
  app, the combinations of systems shared by at least 5 aircraft and the
  rest pooled, and the ADS-L transmitters that also send FLARM or FANET.
- **Return**: of the devices heard on each source in the month, how many
  were heard again on it the month after. The month after is never older
  than the previous month, so both still hold their addresses at that
  moment.
- **Circling per pilot** (PATTERNS.md, section 2): how many aircraft had 1 to 4, 5 to
  9, 10 to 19 and 20 or more thermals, in tenths of their right-hand share,
  and the sums the preference test is computed from (the variance expected
  by chance and the observed one, the aircraft at 80% or more on one side
  and the number chance would give), so that the test is still answered for
  the month. The thermals per aircraft and day are then deleted.
- **Routes of powered aircraft** (PATTERNS.md, section 6, from 7 October
  2026): the flights and distinct aircraft per pair of aerodromes and month.
  The routes per address and day are then deleted.

Months archived before 7 October 2026 were reduced without these, and have
neither.

**Monthly snapshots.** From 7 October 2026, the night the last day of a month
is computed (section 10), the data of every statistics endpoint is stored as
it stood then, with the commit of this file and of the code in force: what
the page showed for that month, frozen. Later changes to a rule change the
live figures, never a snapshot, so a trend over months can be read on
figures computed under the rules of their own time, and the commit says
which. Snapshots are published as downloads under the same licence as the
other data. A month whose last night is computed again has its snapshot
replaced.

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
its size on disk is the price of that. It is not designed to retain data.
From 9 October 2026 the files are kept for seven days instead of four,
because while the measures are being set up a correction has to be tested
against more than three days of traffic, and a day it changes has to be
recomputed before its files are gone. Nothing older than seven days exists.

The recording holds the lines of the OGN feed as they arrive, with their time
of reception, limited to positions inside 35–72° N and 25° W–45° E, together
with the receiving stations' own reports. ADS-B packets from airliners are
left out: those that declare a jet, fly above 15,000 ft or faster than
200 kt, or are reported on the ground, where an airliner taxis. So is every
packet from a device that asks not to be tracked, in its own id or in the
OGN device database. The files stay on the server,
readable by the service account alone; each hour is compressed once it is
over and deleted after seven days. Only the aggregates of this method leave
the server, as with the live feed.

The recording was meant to be switched off once the measures had settled.
From 7 October 2026 it stays on, because the measures of section 10 are
computed from it: each night a batch reads the previous day of the recording
once and keeps only its aggregates. Some of those questions need a whole day
of an aircraft's positions at once (where a thermal begins and ends, whether
an aircraft sat still for half an hour), which the live service cannot hold
in memory for every aircraft. The batch itself needs only the last day and a half;
the rest of the retention is there to recompute a day.

## 10. Measured each night from the recording

From 7 October 2026 some measures are computed once a night, at 01:30 UTC,
from the recording of the previous UTC day (section 9), and only their
aggregates are kept. The same run computes the measures of PATTERNS.md,
and writes them apart: the tables of this file first, those of PATTERNS.md
second, in two transactions, so a failure of either leaves the other's
figures of the day written, and the day's record says what failed (from the
evening of 7 October 2026). They answer questions that need a whole day of an
aircraft's positions at once. The rules of sections 1 and 2 apply unchanged:
the same sources and exclusions, the time written in the fix, the 5-minute
rules for stale and future fixes, the airborne speeds per kind, the
implausible segments and fixes, one timeline per 24-bit address for flying
time. Devices whose owners asked not to be tracked are never recorded.

One rule differs from the live measures. These take the category of every
packet; the nightly measures take, from 7 October 2026, the category the
address declares most often on that system that day, ADS-B's 0 left out of
the vote. The raw recording of 6 and 7 October 2026 showed why: 0.22% of
FLARM packets and 1.3% of PilotAware's declared another category than the
rest of their address's packets, half of them came through ten receivers of
nearly two thousand, and they carried the 65,536 m altitude 250 times and
the 500 km/h jumps 20 times as often as the other packets. Where the same
fix reached the feed through several receivers, the others carried the
usual category in seven cases out of ten. So a stray packet is a reception
fault far more often than a change of aircraft, and following it would move
a segment, a thermal or a drone into another kind. FANET's switch between
paraglider or hang glider and static object is its ground mode, a real
change, and is kept packet by packet. In the live measures the strays moved
0.09% of the flying time to another kind on those days, and 1% to 3% of the
time of drones.
ADS-B stays out of every flight measure, and appears only as the other
aircraft in the drone encounters of 10.1, where an aircraft with ADS-B Out is
exactly what a drone pilot should know about.

A fix belongs to the UTC day written in it. A segment that begins before
midnight UTC and ends after it is not counted; at that hour it is one to
three in the morning across Europe. The data-quality checks of 10.4 are
counted by the time of reception instead, since a wrong clock is what some
of them count. A day missing hours of recording, because the service was
stopped, is computed from the hours there are, and the figures list the
hours that were missing.

How light aviation flies, as opposed to how visible it is, is measured on
the same feed by the same nightly code and described apart, in
[PATTERNS.md](PATTERNS.md): when each kind flies, circling direction,
thermals, height above the ground through the day, flights, launches and
wave. The split was made on 7 October 2026, because those are different
questions with their own readers, and this file is the method for
electronic conspicuity; the hours of the day and circling direction were
first published here, as sections 10.1 and 10.2 (commit 7d04511, the same
day). What stays here is what concerns conspicuity: drones, encounters
between aircraft, parked transmitters and the quality of the data.

### 10.1 Drones (Question 7)

The question: how much do drones fly where light aircraft fly, how low and
how fast, how far from their pilots, and how often they come close to a
crewed aircraft.

A device declaring category 13 (unmanned aircraft) on any source, or any
device on Remote ID, **declares a drone**. Its flying time follows the
timeline of section 1 with the airborne rule for kinds without a speed of their
own (section 1: at least 10 km/h at either end of a segment), so a hovering
drone counts only while it moves, and the time is a lower bound.

**Which declared drones are drones** (from 7 October 2026, before any figure
of this question was published). The category is a setting of the device,
and the first day of data showed crewed aircraft among the addresses that
sent it: of 145 addresses with a category 13 on 6 October 2026, 13 were also
heard on ADS-B with the emitter category of a crewed aircraft, and others
were in the OGN device database as an ASK-21, a Cessna 172, a DR-400 and the
like. On that day all 11 of the 13 that sent a category 13 in time to be
used turned out to be strays (below); the rule that follows exists for the
crewed aircraft whose device is set to drone for good. How they fly cannot
separate them, since a fixed-wing drone flies like a light aircraft: speed,
height and a take-off roll decide nothing. Two
declarations made apart from the device's setting can. The ADS-B emitter
category is set in the transponder by whoever installed it, and does not
depend on how the FLARM or other device was configured. The aircraft type
in the OGN device database is entered by the owner, and can be stale: a
FLARM moved from a glider to a drone keeps the glider's entry until somebody
changes it. Since the device setting can be wrong and the database entry
can be old, neither decides alone, and the flight breaks a tie between them
only in one way: a thermal (two full turns to one side gaining at least
50 m, as in PATTERNS.md, section 2, from 8 October 2026), which a
drone does not fly.

An address declares a drone for the day only when 13 is its majority
category (section 10) on at least one of its systems. On 6 October 2026,
55 of the 145 addresses that sent category 13 at all sent it in one packet
only, every other packet of theirs declaring another kind of aircraft, and
11 more in 2 to 9 packets: packets decoded wrongly, not devices set up as
drones. Such an address is **stray**, counted on the data-quality page and
nowhere in this question; under the majority rule its time is filed in the
flying time by hour (PATTERNS.md, section 1)
under its usual category. (The first version of this test, the same day,
required category 13 in at least half of the address's packets over all
its systems.) Each address that
does declare a drone is classed once the whole day is in:

- **Crewed** when its ADS-B packets carry the emitter category of a crewed
  aircraft (A1 to A7, B1 to B4), or when the database gives it a crewed type
  (glider, plane, ultralight, helicopter) and it flew at least one thermal
  that day, or when, without a database type, it flew a thermal that gained
  at least 100 m on a day whose largest distance from the start of a
  session (below) exceeds 100 km (added the same day): a drone loitering
  over a point turns, but it does not climb in circles on a cross-country
  of a hundred kilometres, which is what a glider or a paraglider does. It is left out of every measure of this question, and counted
  per system on the data-quality page (10.4), by which evidence.
- **Confirmed drone** when its ADS-B emitter category is B6 (unmanned
  aircraft), or when the database gives it the drone type. A device heard on
  Remote ID is a confirmed drone on its own, before every other test: it is
  the broadcast identification the EU rules define for drones (Regulation
  (EU) 2019/945), sent by drones and their add-on modules, and a crewed
  aircraft has no reason to carry one (decided on 7 October 2026; the
  first version of this rule, the same day, left Remote ID without a
  database entry uncertain).
- **Uncertain** otherwise: no ADS-B emitter category (an ICAO address
  without one included) and no database type, or a database type that
  disagrees with the device and a flight with no thermal to settle it.
  Uncertain drones stay in the question, counted apart from the confirmed
  ones.

The stray test comes before all of this, so a crewed aircraft that sent a
single category 13 counts as stray. Crewed evidence is read next, so an
address with evidence of both kinds counts as crewed. A crewed aircraft's
flying time is filed in the flying time by hour (PATTERNS.md, section 1)
under unknown category rather than as a drone,
since neither the transponder nor the database gives an OGN category. The database is honoured as everywhere: a device whose owner asked
not to be tracked is left out, and the type of one not to be identified is
not used.

Each airborne segment is filed by the **1-degree cell** where it began
(about 110 by 77 km at the latitude of the Alps; a finer map of so few
devices could point at one operator), by **height above the ground** (0–50, 50–120, 120–300, above
300 m, from the terrain model of section 3; 120 m is the ceiling of the
open category in the EU rules for drones), by **speed** (the mean of its two
ends: under 20, 20–50, 50–100, over 100 km/h), and by the set of systems the
drone's address was heard on that day (platforms that only relay others are
left out).

**Extent.** For each drone and day, the largest distance from the first fix
of a session to any later fix of it (sessions end at a silence over 20
minutes), in bands of under 1, 1–3, 3–10 and over 10 km. The first fix is
taken as where the remote pilot stands, who must keep a drone in the open
category within sight, a few hundred metres for a small one. An extent above
1 km therefore **suggests** flight beyond the line of sight; it cannot prove
it, since the pilot may move or have observers, and the first fix may come
after take-off. A session is used rather than the whole day so that an
operator who flies at two sites is not counted as one flight between them.

**Encounters.** A drone fix and a fix of a crewed aircraft in flight
(categories 1 to 9, at the airborne speed of its kind, any source including
ADS-B) within **10 seconds**, **1 km** horizontally and **150 m** vertically,
both with an altitude. One encounter is counted per pair of aircraft in 10
minutes, filed by its closest horizontal distance (under 300, 300–600,
600–1,000 m), the kind of the other aircraft, the systems each address was
heard on that day, and whether the two share at least one of them. Sharing
a system means both were heard on it by the network. Whether either could
have shown the other to its pilot depends on what each device decodes and
displays, which the feed does not say, so nothing more is claimed from it.

### 10.2 Sharing the air: crewed aircraft of different kinds (experimental)

The question: how often do aircraft of different kinds, free flight,
gliders, powered aircraft and helicopters, come close to each other, and on
which systems is each of them heard when they do. It is published, from
7 October 2026, on an experimental page beside the drones of Question 7.

One aircraft is one 24-bit address. Its kind is the category it declares
most often that day over all its systems, ADS-B included (ADS-B's 0 left
out of the vote): free flight (paraglider and hang glider), glider, powered
aircraft (tow plane, powered, jet) and helicopter. Every source counts,
ADS-B included, one fix per address and second, and both aircraft must be
airborne at the speed of their kind (section 1). An **encounter** is a fix
of each within **10 seconds**, either within **1 km horizontally and 150 m
vertically** or, the second threshold, within **300 m and 100 m**. One is
counted per pair of aircraft and threshold in 10 minutes, filed by its
closest horizontal distance, by the closing speed at that moment (the
difference of the two velocities: under 50, 50–100, 100–200, 200–400, over
400 km/h), by the systems each address was heard on that day, and by
whether the two share at least one radio system (ADS-B counts as one) or
any system at all. Two aircraft of the same kind are left out: the
question is about kinds that fly under different rules and carry
different equipment.

**Flying together.** A pair that stays within 300 m at a relative speed
under 20 km/h for at least 60 seconds (contacts at most 20 s apart) is
flying together: an aerotow, a formation, or one pilot carrying two devices
under two addresses. None of its encounters count that day, and the tows
among such pairs are counted as launches (PATTERNS.md, section 7). Crossing at
100 km/h, two aircraft stay within 300 m for about 20 seconds; a tow lasts
minutes. The first count of these encounters, the same day, showed why the
rule is needed: the close encounters between gliders and powered aircraft
had a median closing speed of 7 km/h, and 212 of 259 were under 100 m. A
second rule, kept beside it, catches pairs within 300 m for more than
5 minutes over more than 3 km whatever their relative speed.

**What this measure cannot see.** An encounter is counted only when both
aircraft transmit something the OGN network hears. The encounters most
likely to end in a collision, where one of the two aircraft is invisible to
the other because it carries nothing or nothing compatible, are therefore
outside the measure by construction, and so are aircraft no receiver hears.
The figures are a floor on how often aircraft of different kinds come
close, never an estimate of the risk. Nor does sharing a system mean that
either aircraft could have warned the other: a warning needs receiving
equipment that decodes the other's signal and shows it to the pilot, and
which devices receive which systems is a separate question, to be answered
from the manufacturers' documentation in a table of its own. Shared
systems are reported as a fact about the network, nothing more.

Encounters are published as monthly aggregates by kind pair, band and
systems only, never as events, dates, times or fine maps, so that no
encounter between two identifiable aircraft can be read from them.

### 10.3 Parked aircraft transmitting

The question: how much of each system's traffic comes from aircraft sitting
on the ground with the transmitter on. A parked transmitter uses airtime that
aircraft in flight share (FANET slows every device in range down as their
number grows, section 2), drains a battery, and shows on other pilots'
displays as traffic.

An aircraft counts as parked when it declares a crewed category (gliders to
jets, categories 1 to 9) and all its fixes on one system stay inside a box
whose diagonal is under **100 m** for at least **30 minutes**, with no
silence over 20 minutes and no change of category, and the median of its
height above the terrain model, read once a minute, lies between **−60 and
+60 m**. The box is twice the position noise of a phone, and smaller than
any field anybody taxis or ground handles in; half an hour excludes a
briefing or a launch queue; the height band is the error of the terrain
model near relief (section 3) with the altitude offsets seen on the ground,
0 to +8 m on every system. A paraglider or hang glider pilot waiting at
take-off with the instrument on for half an hour is counted too.
ADS-B is left out, since the recording drops ADS-B surface reports
(section 9). Per day, system and category the data keep the parked aircraft,
the seconds and the packets received.

### 10.4 Data quality

The question: which errors in the feed come from the way a system or a
receiver is set up, and could be fixed by whoever runs it. Findings are
published per system and per OGN receiver, by name; devices appear only as
counts (decided on 7 October 2026). Each check is a count over a stated
total, per day.

Per system:

- **Fixes more than 60 seconds late** on arrival, and **fixes more than
  5 seconds ahead** of their arrival, of all fixes: a clock that is wrong, or
  positions held back and sent in bursts. The measures already ignore fixes
  more than 5 minutes late (section 1).
- **Altitude of 60,000 m or more**, of the fixes with an altitude: 2¹⁶ m,
  65,536 m, is what a small negative altitude becomes in an unsigned 16-bit
  field. **Altitude exactly 0**, of the same: usually no altitude, sent as
  zero.
- **Positions within 1 degree of 0,0** and **positions without a time**
  (the time field sent as underscores), of all position packets.
- **Heading over 360 degrees**, of the fixes with a course.
- **Free-flight fixes beyond what a paraglider or hang glider can do**
  (section 1), of the free-flight fixes.
- **Addresses with 3 or more jumps** over 500 km/h and 2 km between
  successive fixes, of the addresses on the system: one address shared by
  several devices, or corrupt positions.
- **Devices changing category within the day**, of the devices declaring
  one, counting only categories declared in at least two packets. A FANET
  instrument switching between paraglider or hang glider and static object
  is its ground-tracking mode after a landing, counted under a check of its
  own.
- **Devices declaring a drone** (10.1), of the addresses that sent
  category 13 on the system: those where it was a stray packet, those with
  crewed evidence, in all and by evidence (ADS-B emitter category, database
  type with a thermal, climbing thermal on a long day), those confirmed, and
  those left uncertain, with how many of these have a crewed database type
  and no thermal.
- **Stray categories**: packets declaring another category than their
  address's majority on the system that day (section 10), of the packets
  declaring one; ground mode and ADS-B's 0 are not strays.

Per receiver, from its own position reports and from the radio packets it
forwards (the receiver is the last element of the packet's path; ADS-B is
left out):

- **Moved**: its reported position more than 2 km from its first report of
  the day. A receiver in a car, or one whose configured position is wrong
  part of the time, places the aircraft it hears wrongly whenever their
  position is decoded from its own (section 1).
- **Altitude**: the median of its announced altitude more than 500 m from
  the terrain model under it.
- **Late**: more than 5% of the packets it forwards (at least 100) more than
  60 seconds late.
- **Stray categories**: 2% or more of the radio packets it forwards (at
  least 1,000 with a category) declare another category than their
  address's majority. The median receiver was at 0.01% on 6 and 7 October
  2026 and the worst two near 45%; 2% is two hundred times the median and
  still catches a station whose strays are a minority of its traffic.
- **Altitude offset**: FLARM devices send their height above the ellipsoid
  and the receiver converts it to height above sea level. For every FLARM
  fix heard by two or more receivers, the altitudes they forward are
  compared. A receiver's offset is the median of its differences with the
  others; a receiver heard mostly beside one faulty neighbour would show that
  neighbour's error reversed, so the figure kept is the median against the
  partners whose own offset is within 20 m. Flagged above **20 m**, with at
  least **50** fixes shared with such partners. On 3 October 2026 two
  receivers showed +41 to +48 m, the size of the geoid above the ellipsoid in
  central Europe, as if they did not convert.

Receivers are listed only when flagged, beside how many were judged.

## 11. Known limits

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
- One aircraft is one 24-bit address, while Naviter's ids are unique only
  within Naviter: two Naviter users could in principle share an address with
  each other or with a radio device.
- The systems of one address may declare different categories; each measure
  takes the category of the packet it is counting.
- A restart of the service loses the totals not yet written (at most 15
  minutes) and every silence that spans it; nothing is counted twice, and no
  silence is invented.
- The altitude reference of drones on the feed is not documented. The Remote
  ID standards carry a height above the WGS84 ellipsoid, which lies 40 to
  55 m above sea level in Europe; if that is what arrives, a drone looks that
  much higher than it is, which moves it across the 50 m band of section
  10.1 and makes the vertical margin of an encounter smaller in effect.
- The live service still files a crewed aircraft declared as a drone under
  drones in the monthly flying time (section 1, `monthly_hours`); only the
  nightly measures of section 10 set it aside.
- A declared drone heard by no ADS-B and with no database entry stays
  uncertain however it flies: the method does not judge identity from
  flight, except for a thermal against a crewed database entry.
- The nightly measures need the raw recording: a day the service was stopped
  for has its missing hours missing for good.
