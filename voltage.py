import time, sys, uselect
from machine import Pin, UART
from rp2 import PIO, asm_pio, StateMachine
import math

# Set the Gate of the Driver MOSFET to HIGH for a short period, driving the voltage down. Glitches the STM32. (See sketch schematics)
@asm_pio(set_init = rp2.PIO.OUT_LOW, out_shiftdir=PIO.SHIFT_RIGHT, autopull=True, pull_thresh=16)
def drop_voltage():
    # Delay and Glitch durations are all inside the 32bit string.
    # Bits 0-4 is used for delay duration (32 cycle loop)
    # Bits 5-9 is used for delay duration (2 cycle loop)
    # Bits 10-14 is used for glitch duration
    pull()
    mov(isr, osr)
    
    # Start of program
    label("Prog_Start")
    
    # Move/Reloads the glitch timing and delay into the x and y registers
    out(x, 5)		#x contains the delay duration (2 cycle loop) 
    out(y, 5)		#y contains the delay duration (32 cycle loop)
    #nop()			#The 5 LSB of OSR should now contain the glitch duration
    
    # Waits for Rising Edge from Pin 4 before glitching with Pin 3
    wait(1, pin, 0)
    
    # 2 cycle delay
    label("delay_short")
    nop()		
    jmp(x_dec, "delay_short")
    
    # Skips 32 cycle delay and goes to glitch
    jmp(not_y, "Glitch")
    
    # 32 cycle delay
    label("delay_long")
    nop()		[30]
    jmp(y_dec, "delay_long")
    
    
    label("Glitch")
    # Moves the glitch timing to scratch register X
    mov(x, osr)
    
    # Set Pin 3 to High. Induces Glitch
    set(pins, 1)
    
    # Glitch timing
    label("glitch_timing")
    nop()
    jmp(x_dec, "glitch_timing")

    # Set Pin 3 to Low. Return opertaions as per normal
    set(pins, 0)
    
    # Delay to allow the STM to get back to operating voltage
    set(x, 31)
    label("delay_low_outer")
    set(y, 2)
    label("delay_low_inner")
    nop()			[31]
    jmp(y_dec, "delay_low_inner")
    jmp(x_dec, "delay_low_outer")
    
    # Reloads the original value back into OSR
    mov(osr, isr)
    # Wait for Falling edge so the PIO does not continually glitch
    wait(0, pin, 0)
    jmp("Prog_Start")
    
# Set Pin 3 output drive strength to be 12mA, slew to be fast. Refer to rp2040 datasheet, PADS_BANK0
machine.mem32[0x4001c010]=0x7f

# Open a file from the Pico's onchip flash memory
fd = open("output.txt", "a")

# Start flag
start_flag = 0

# Bit string to be sent to PIO asm through TX FIFO
bit_string = 0

# Switch ON the on-board LED.
led = Pin("LED", Pin.OUT)
led.value(1)

# Initialize UART
# When connected in Normal operation, parity = None
# When connected in Bootloader mode, partity = 0 (EVEN)
uart = UART(0, baudrate=115200, tx=Pin(12), rx=Pin(13))
uart.init(bits=8, parity=None, stop=1)

# Sets up the comms btw Pico and Thonny IDE
spoll=uselect.poll()
spoll.register(sys.stdin,uselect.POLLIN)

def read1():
    return(sys.stdin.read(1) if spoll.poll(0) else None)

def readline():
    c = read1()
    buffer = ""
    while c != None:
        buffer += c
        c = read1()
    return buffer
                
def get_glitch_duration():
    print("Glitch timing is accurate to 20ns")
    print("Enter glitch duration(ns) btw 160 to 700: ", end = "")
    while True:
        glitch_duration = readline()
        if len(glitch_duration) != 0:
            try:
                # GLitch duration to be determined later. According to research, should be approximately 200ns-ish
                if (int(glitch_duration) < 160 or int(glitch_duration) > 700):
                    raise Exception
                if (int(glitch_duration) % 10 != 0):
                    raise Exception
                
                return glitch_duration
                
            except:
                print("Error, please enter an integer of multiple 10 between 160 and 700.")
                print("Enter glitch duration(ns): ", end = "")

def get_delay_duration():
    print("Enter delay duration(ns) btw 30 to 20000: ", end = "")
    while True:
        delay_duration = readline()
        if len(delay_duration) != 0:
            try:
                # Cap is 21,120 i believe
                if (int(delay_duration) < 30 or int(delay_duration) > 20000):
                    raise Exception
                if (int(delay_duration) % 10 != 0):
                    raise Exception
                
                return delay_duration
                
            except:
                print("Error, please enter an integer of multiple 10 between 30 and 20000.")
                print("Enter delay duration(ns): ", end = "")

def calc_glitch_cycles(duration):
    #nop() [11] gives a reliable glitch time of 160ns. Each extra clock cycle adds 10ns.
    cycles = 4 + (int(duration) - 160) / 20
    return cycles


def filter(data):
    data = data.replace("i = 0 j = 0 ctrl = 1 \n\r", "")
    data = data.replace("i = 0 j = 1 ctrl = 2 \n\r", "")
    data = data.replace("i = 1 j = 0 ctrl = 3 \n\r", "")
    data = data.replace("i = 1 j = 1 ctrl = 4 \n\r", "")
    return data
    
print("State Machine's Frequency is set to 100Mhz")
print("Press ENTER to start!")
print("Press ENTER during execution to change the glitch timing and delay duration!")

while True:
    # Prints output of STM32 to Thonny IDE
    if uart.any() and start_flag == 1:
        try:
            # Removes the trailing empty spaces and decodes the string received
            data = uart.read().decode('ascii').rstrip('\xff').rstrip('\x00')
            #data = uart.read()
            
            # Prints data to terminal
            print(data)
            
            # Removes the expected data and only adds abnormal data to the file
            data = filter(data)
            if (len(data) > 0):
                fd.write(data)

        except:
            data = uart.read()
            print(data)
    if (len(readline()) != 0):
        start_flag = 0
        # Set up the State Machine and puts the glitch and delay timings on the TX FIFO
        glitch_time = get_glitch_duration()
        glitch_cycles = calc_glitch_cycles(glitch_time)
        bit_string = int(glitch_cycles) << 10		#Logical shift left by 10 bits
        print(int(glitch_cycles))
        
        delay_duration = get_delay_duration()
        if (int(delay_duration) < 650):
            delay_cycles = (int(delay_duration) - 30 )/20	# At least 3 cycles is being used for delay. Need to account for them
            bit_string = bit_string + int(delay_cycles)		# No need to shift 2 cycle delay as they are the 5 LSB
        else:
            long_delay_cycles = math.floor((int(delay_duration) - 30) / 320) 
            delay_cycles = math.floor((int(delay_duration) - (long_delay_cycles *320) ) / 20)
            long_delay_cycles = long_delay_cycles - 1 # Loops in PIO index starts at 0
            delay_cycles = delay_cycles -1
            bit_string = bit_string + (long_delay_cycles << 5)
            bit_string = bit_string + delay_cycles
            print(f"long delay cycle: {long_delay_cycles}")
        print(f"Delay Cycles: {delay_cycles}")
        print(f"Bit_string: {bit_string}")
        
        # Instantiates the State Machine
        sm = StateMachine(0, drop_voltage, freq = 100_000_000, set_base = Pin(3), in_base = Pin(4))
        sm.put(int(bit_string))
        sm.active(1)
        start_flag = 1
