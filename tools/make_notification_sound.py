"""Create an original short stereo-friendly PCM chime, no external audio."""
from pathlib import Path
import math,struct,wave

target=Path(__file__).resolve().parents[1]/'assets'/'completion.wav'
rate=44100;duration=.9
notes=((0,.28,659.25),(.21,.50,830.61),(.42,.87,987.77))
samples=[]
for i in range(round(duration*rate)):
    t=i/rate;value=0
    for start,end,frequency in notes:
        if start<=t<end:
            age=t-start;envelope=min(1,age/.012)*min(1,(end-t)/.06)*math.exp(-age*3)
            value+=envelope*(math.sin(2*math.pi*frequency*age)+.22*math.sin(4*math.pi*frequency*age))*.40
    samples.append(struct.pack('<h',round(max(-.9,min(.9,value))*32767)))
with wave.open(str(target),'wb') as output:
    output.setnchannels(1);output.setsampwidth(2);output.setframerate(rate);output.writeframes(b''.join(samples))
print(target)
