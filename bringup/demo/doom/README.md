# Running the DOOM demo

## 1. Get the WAD

Download the shareware DOOM1.WAD from the [Internet Archive](https://archive.org/details/doom_20230531)
and drop it in this folder. It is not redistributed here.

## 2. Generate the headers

```bash
python3 wad2c.py DOOM1.WAD --map E1M1 --subsectors --nodes --heights -o e1m1_ssec.h
python3 gen_trig.py
python3 wadgfx.py DOOM1.WAD -o wadgfx.h
```

That gives you three headers: the BSP tree with per-wall sector heights, a Q0.7
sine table, and the pistol and status bar ripped from the WAD as RGB565.

## 3. Move them into the sketch

```bash
mv e1m1_ssec.h trig_q7.h wadgfx.h doom_driver/
```

Your setup should now look like this:

![Correct Setup](../../../docs/CorrectSetup.png)

## 4. Flash the FPGA first

Program the Tang Nano 20K with the Clementine bitstream in Gowin Programmer.
This has to happen before the ESP32 runs, since the panel's data line is on
Clementine's MISO and nothing reaches the screen without it.

The bitstream is an SRAM load, so repeat this after any power cycle.

## 5. Flash the driver

Open `doom_driver/doom_driver.ino` in the Arduino IDE, select the ESP32-S3,
and upload.

## 6. Play

```bash
python3 walk.py
```

WASD to move, `q` to quit. If you are using WSL, you may need to attach the port first by using usbipd:

```powershell
usbipd list # find your MCU's BUSID
usbipd attach --wsl --busid <your-esp32-busid>
