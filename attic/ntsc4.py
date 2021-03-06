#!/usr/bin/python3

# 11/29/14:  I worked out a new way to determine sync locations by using a butterworth filter to get a sync rate (0-1)

import numpy as np
import scipy as sp
import scipy.signal as sps
import sys
import os

import io

from scipy.interpolate import interp1d
import matplotlib.pyplot as plt

# support code

 
def dosplot(B, A):
	w, h = sps.freqz(B, A)

	fig = plt.figure()
	plt.title('Digital filter frequency response')

	ax1 = fig.add_subplot(111)

	plt.plot(w * (freq/np.pi) / 2.0, 20 * np.log10(abs(h)), 'b')
	plt.ylabel('Amplitude [dB]', color='b')
	plt.xlabel('Frequency [rad/sample]')

	plt.show()


# old code

freq = 4.0 # output frequency - 4fsc

freq_mhz = (315.0 / 88.0) * freq
freq_hz = freq * 1000000.0
blocklen = 8192 

# inframe is a 1685x505 uint16 buffer.  basically an ntsc frame with syncs removed

color_filter = sps.firwin(17, 0.1 / (freq_mhz / 2), window='hamming')

frame = np.empty([505, 844], dtype=np.uint16)

# set up sync color heterodyne table first 
bhet = np.empty(4096, dtype=np.complex)
for i in range(0, 4096):
	bhet[i] = complex(np.cos(((i / freq) * 2.0 * np.pi) + (33.0/180.0)), -(np.sin(((i / freq) * 2.0 * np.pi) + (33.0/180.0))))
	bhet[i] = complex(np.cos(((i / freq) * 2.0 * np.pi) + (0.0/180.0)), -(np.sin(((i / freq) * 2.0 * np.pi) + (0.0/180.0))))

def printerr(*objs):
	print(*objs, file=sys.stderr)
	return

def burst_detect(line, loc = 0):
	level = 0
	phase = 0

	obhet = np.empty(100, dtype=np.complex)

	obhet = bhet[loc+00:loc+100] * line[loc+00:loc+100]

	obhet_filt = sps.lfilter(color_filter, [1.0], obhet)
	obhet_levels = np.absolute(obhet_filt)
	obhet_angles = np.angle(obhet_filt)

	for i in range(0, 100):
#		print(i, line[i], obhet_levels[i], obhet_angles[i])
		if (obhet_levels[i] > level) and (obhet_levels[i] < 10000):
			level = obhet_levels[i]
			phase = obhet_angles[i]

	return [level, phase]

from scipy import interpolate

# This uses numpy's interpolator, which provides very good results
def scale(buf, begin, end, tgtlen):
	ibegin = np.floor(begin)
	iend = np.floor(end)

	linelen = end - begin

	dist = iend - ibegin + 1 
	arr = np.linspace(0, dist, num=dist)
	print(dist, begin, ibegin, ibegin + dist)
	spl = interpolate.splrep(arr, buf[ibegin:ibegin + dist])
	arrout = np.linspace(begin - ibegin, linelen, tgtlen)
						
	return interpolate.splev(arrout, spl)

def wrap_angle(angle, tgt):
	adjust = tgt - angle
	if (adjust > (np.pi)):
		adjust -= 2 * np.pi
	elif (adjust < (-np.pi)):
		adjust += 2 * np.pi

	return adjust

# new code

# sync filter, should be usable for 4x and 8x fsc
#f_sync_b, f_sync_a = sps.butter(1, 0.5 / 14.318)
f_sync_b = sps.firwin(17, 0.5/14.318)
f_sync_a = [1.0]

f_id_b, f_id_a = sps.butter(3, 0.002)
f_id_b, f_id_a = sps.butter(3, 0.004)

def is_sync(x):
	return 1 if x < 20000 else 0

scale_line = ((63.5 + 9.2) / 63.5)
scale_linelen = 910.0 * ((63.5 + 9.2) / 63.5) # add to non-TBC line length to get second hblank
phasemult = 1.591549430918953e-01 * 8 # this has something to do with pi/radians, forgot what 

tgt_angle = 0

def process(indata):
	global tgt_angle 

	tcount = 0

	indata = indata[33000:147000]

	vis_sync = np.vectorize(is_sync)
	indata_bool = vis_sync(indata) 
	
	indata_bool_filt = sps.lfilter(f_id_b, f_id_a, indata_bool)[163:]

	insync = -1 
	line = 0

	i = 0
	prev_peak = 0
	prev_begin = 0
	prev_end = 0
	prev_len = 0
	while ((i + 4096) < len(indata_bool_filt)):
		peak = i + np.argmax(indata_bool_filt[i:i+1300])

		peakval = indata_bool_filt[peak]

		begin = -1
		for j in range(peak, peak - 300, -1):
			if (begin == -1) and (indata[j] > 12000):
				begin = j

		end = -1
		for j in range(peak, peak + 300, 1):
			if (end == -1) and (indata[j] > 12000):
				end = j

		print("l ", line, peak, begin, end, end - begin, begin - prev_begin, peak - begin, indata_bool_filt[peak], indata[peak])

		if (insync <= 0) and (peakval < .20):
			if ((np.fabs(begin - prev_begin) - 910) < 100):
				print(line, "sync - half line detected")
				insync = 2
			else:
				print(line, "sync start")
				insync = 1

		if (insync == 0):
			# detect error condition - if so, reuse previous begin/end
			if (np.fabs(end - begin - 131.0) > 3):
				begin = prev_begin + prev_len
				end = prev_end + prev_len
				print(line, begin, end, "error")

			send = prev_begin + ((begin - prev_begin) * scale_line)
			print(end, send)

			ibegin = np.floor(prev_begin)
			out1 = scale(indata[ibegin:ibegin+2300], prev_begin - ibegin, send - ibegin, scale_linelen)
			a1 = burst_detect(out1)	
			a2 = burst_detect(out1[910:])	
		
			if (tgt_angle == 0):
				tgt_angle = np.pi / 2.0 if wrap_angle(a1[1], np.pi / 2.0) < (np.pi / 2.0) else -np.pi/2.0
			else:
				tgt_angle = -tgt_angle
				
			print("scale 0", prev_begin, send, a1, a2)

			count = 1
			err = (np.fabs(wrap_angle(a1[1], tgt_angle)) * 1) + np.fabs(wrap_angle(a2[1], tgt_angle))
			while (err > .01) and a1[0] > 1500 and a2[0] > 1500 and count < 10:
				begin_offset = wrap_angle(a1[1], tgt_angle) * phasemult 
				end_offset = wrap_angle(a2[1], a1[1]) * phasemult 
				prev_begin += begin_offset - (end_offset * .07)
				send += begin_offset + (end_offset * 1.0)
				ibegin = np.floor(prev_begin)
				out2 = scale(indata[ibegin:ibegin+2300], prev_begin - ibegin, send - ibegin, scale_linelen)
				a1 = burst_detect(out2)	
				a2 = burst_detect(out2[910:])	
				print("scale", count, " ", prev_begin, send, a1, a2)
				err = (np.fabs(wrap_angle(a1[1], tgt_angle)) * 1) + np.fabs(wrap_angle(a2[1], tgt_angle))
				count += 1

			tcount += (count - 1)

#			print("scaler", prev_begin, begin, end = ' ' ) 
#			rescale = (send - prev_begin) / (scale_linelen * 2)
#			begin = prev_begin + (1820 * rescale) 
#			print(rescale, begin) 

		# needs to be done first becausse the first line is written normally
		if (insync >= 1):
			if (peakval > .14) and (peakval < .17):
				print(line, "sync over")
				cline = 10 if insync == 1 else 11
				insync = 0

		i = peak + 800
		prev_len = end - prev_end 
		prev_peak = peak
		prev_begin = begin
		prev_end = end 
		line = line + 1

#	print(i, i - prev_i, indata_bool_filt[i])


#	print(len(indata), tcount)
#	exit()

#	for i in range(0, len(indata_bool_filt), 50000):
#		print(i, indata_bool_filt[np.argmax(indata_bool_filt[i:i+50000])])

	offset = 59000
	length = 12000
	indata = indata[offset:offset+length]
	indata_bool_filt = indata_bool_filt[offset:offset+length]

	print(len(indata))

	plt.plot(range(0,len(indata_bool_filt)), indata_bool_filt)
#	plt.plot(range(0,len(indata_bool)), indata_bool)
	plt.plot(range(0,len(indata)), indata / 65536.0)
	plt.show()
	exit()

	return 1820*505

# buffer enough to hold two entire frames and change - probably only need one, but ram is cheap nowadays 
buftgt = 1820 * 1200

# ???
indata_valid = 0
indata = np.empty(buftgt, dtype=np.uint16)

def main(argv=None):
	outfile = open("test.tbc", "wb")
	infile = sys.stdin.buffer

	done = 0

	inbuf = infile.read(buftgt * 2)
	indata = np.fromstring(inbuf, 'uint16', buftgt)

	while (done == 0):
		keep = process(indata)
		indata = indata[keep:]

		toread = buftgt - len(indata)
		inbuf = infile.read(toread * 2)
		
		print(toread * 2, len(inbuf), len(indata))

		if (len(inbuf) < toread):
			done = 1

		indata = np.append(indata, np.fromstring(inbuf, 'uint16', int(len(inbuf) / 2)))

		print(len(inbuf), len(indata))

if __name__ == "__main__":
    sys.exit(main())

