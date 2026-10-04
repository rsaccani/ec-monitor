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
PilotAware as OGPAW and OGNPAW. Each is treated as one source, so a device
heard under two of them is followed as one stream and counted once. Until
15:19 UTC on 4 October 2026 they were followed apart, which split one
FLARM into two sparser streams that looked less visible than the device was
and counted its flying time twice; the October 2026 figures mix the two rules
up to that time.

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
glider, `'` glider, `^` powered aircraft, `X` helicopter, `O` balloon).

**Time** is the instant of the fix written in the packet. A packet whose fix
is more than 5 minutes older than its arrival is ignored, since it would open
a gap that never happened, and so is a packet older than the last one received
from the same device. An app that regains coverage and sends its stored
positions in a burst therefore counts as invisible for the minutes it was
silent, which is intended: nobody could see the aircraft at the time.

**Airborne.** Two consecutive positions of a device on a channel form a
segment. A segment counts as airborne when the ground speed at either end is
at least 10 km/h, so a pilot who has landed or is walking up to take off does
not dilute the measures.

**New session.** A segment longer than 20 minutes is treated as a new session
(the device was switched off, or the pilot drove to another site) by the
measures of sections 2, 3 and 5. Section 4 counts it as a disappearance.

**Implausible segment.** A segment implying more than 500 km/h is excluded:
some addresses are shared by several devices at once, and followed as one
device they produce jumps of hundreds of kilometres.

**ADS-B** is counted among the sources but left out of the measures of
visibility, being almost entirely airliners.

**Flying time** of each kind of aircraft is measured once per aircraft. Every
fix of the same 24-bit address joins one timeline, whichever source and
channel it came by, and the airborne segments of that timeline are added up
with the rules above. A paraglider whose instrument sends FLARM, FANET and
ADS-L under one address counts its time once, and so does a pilot whose phone
app uses the address of the aircraft's own device. Two devices with different
addresses on one aircraft still count twice, and an aircraft nobody hears
counts nothing. The measure starts at 15:19 UTC on 4 October 2026.

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
whose position comes from its own reports in the feed. The receiver's
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
  of airborne time in it and their last position stayed within 300 m of the
  true one for at least 95% of that time.
- The airborne time of each kind of aircraft is summed per cell over every
  source and channel, radio included, so that pilots who carry no app count
  too.
- The figure is the share of that time spent in covered cells. Cells with
  less than 2 hours of app data count as not covered, so the share is a lower
  bound, and it is published only when less than half of the time falls in
  such cells. A pilot heard through two sources at once counts on both, which
  inflates the hours and leaves the share alone as long as such pilots fly in
  covered and uncovered cells alike.

## 9. What is kept

Monthly lists of device addresses per source and channel, for counting
devices; daily and monthly totals per source, channel, category, height band
and cell for the measures above; reception counts by angle; prediction errors
by bin. No track and no position of any aircraft is stored.

A device address can be traced to an aircraft and its pilot, so the lists of
addresses are kept for the current and the previous month only. Once a month
is older than that, its lists are reduced to the counts the published figures
use (devices per month by address type, category, source and channel, and how
many were heard on two different days) and the addresses are deleted. The
previous month is kept whole so that the share of devices heard again from
one month to the next can be measured; a source whose ids change often shows
almost none. The service checks every six hours, so a month's addresses go
within a few hours of the end of the following month.

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
