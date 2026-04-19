import simpy
from collections import deque
import math
import matplotlib.pyplot as plt

# -----------------------------
# PARAMETERS
# -----------------------------
CASES_PER_YEAR=240000
WORKING_DAY_PER_YEAR=240
CASES_PER_DAY=CASES_PER_YEAR/WORKING_DAY_PER_YEAR   
TILES_PER_CASE=24
TILES_PER_DAY=CASES_PER_DAY*TILES_PER_CASE  
PRESS_NUMBER_OF_SHIFTS=3
MILL_NUMBER_OF_SHIFTS=1
MILLING_TILES_PER_SHIFT=TILES_PER_DAY/MILL_NUMBER_OF_SHIFTS
TILE_WEIGHT_KG = 0.375
SHIFT_1 = 8 * 60 * 60
SHIFT_3 = 24 * 60 * 60
POWDER_PER_SHIFT_KG = MILLING_TILES_PER_SHIFT * TILE_WEIGHT_KG
POWDER_FLOW_RATE=POWDER_PER_SHIFT_KG/SHIFT_1
RESIDENCE_TIME_MIXER=5*60 #MINUTES OF RESIDENCE IN THE MIXER
HOPPER_SIZE=POWDER_FLOW_RATE*RESIDENCE_TIME_MIXER

#PRESS AND GLAZE CT CALCULATIONS
PRESS_CT =  (PRESS_NUMBER_OF_SHIFTS * SHIFT_1) / TILES_PER_DAY  # time to press one tile based on daily demand and press capacity
GLAZE_CT = PRESS_CT   # glaze time MUST BE EQUAL TO PRESS_CT to avoid bottleneck at glaze step

#MILL CT CALCULATION
MILL_CT = .9 #instanteneous milling for simplicity
MIX_CT = TILE_WEIGHT_KG/POWDER_FLOW_RATE #mixing time based on press capacity and tile weight
DRY_CT = MIX_CT

#KILN DATA
KILN_RESIDENCE = 14 * 60 * 60
KILN_CT = PRESS_CT
KILN_CAPACITY = math.ceil(KILN_RESIDENCE/KILN_CT )

PACK_CT = 24.0

FIFO_CAPACITY = 240
SUPERMARKET_CAPACITY = 28800

NUM_TILES = 8000*3


#MILLED POWDER SILO CAPACITY AND INITIAL LEVELS
SILO_CAPACITY_IN_DAYS = 1.2
SILO_CAPACITY_KG = POWDER_FLOW_RATE*MILL_NUMBER_OF_SHIFTS*SHIFT_1*SILO_CAPACITY_IN_DAYS#SILO CAPACITY TO HOLD 1.2 DAYS OF MILLED POWDER (to allow for some buffer)
INITIAL_SILO_LEVELS = [SILO_CAPACITY_KG, SILO_CAPACITY_KG]#START WITH FULL SILOS TO AVOID INITIAL STARVATION

supermarket_count = 0#COUNTER TO TRACK NUMBER OF TILES IN SUPERMARKET (for logging purposes)

CUSTOMER_PULL_CT = 5.0 #time between customer pulls (for simulation purposes, we can adjust this to see how it affects the system)

finished_tiles = 0
finished_cases = 0



TRUCK_CAPACITY=3000#TRUCK CAPACITY IN KG (based on 3000 kg max load and 0.375 kg per tile, this allows for 8000 tiles per truck, which is more than our daily demand, so we can treat it as a daily pull)
TRUCK_CASES = math.ceil(TRUCK_CAPACITY/TILE_WEIGHT_KG/TILES_PER_CASE)
TRUCKS_PER_DAY=3
TRUCK_INTERVAL = 24*60*60/TRUCKS_PER_DAY

staging_cases = int(TRUCK_CASES * 3)
staging_tiles = 0
tiles_completed=0
cases_to_pack=0

# -----------------------------
# DATA COLLECTION FOR PLOTTING
# -----------------------------
time_log = []
fifo_log = []
supermarket_log = []
staging_log = []
silo0_log = []
silo1_log = []

STOP_SIM=False
""" -----------------------------
SILO OBJECT
The SiloSystem class models two parallel powder silos that store dried powder coming from the dryer and supply powder to the press.
It enforces material balance, flow direction, and silo balancing logic.
This class is the only place where powder inventory is stored and updated.
It needs to be modified for a two materails feeding scenario
 -----------------------------"""
class SiloSystem:
    def __init__(self, initial_levels):
        self.levels = initial_levels[:]  # kg in each silo

    def feed_from_dryer(self, env, tile_id, weight,powder_color):# weight is the weight of the tile being fed into the silo, which corresponds to the amount of powder being added to the silo after drying
        """Dryer adds dried material to the corresponding silo based on powder color. In a single material scenario, we can just feed into the less full silo to keep them balanced. In a two material scenario, we need to feed into the silo corresponding to the powder color."""
        if powder_color=="red":
            idx =0
        else:
            idx=1    
        self.levels[idx] += weight
        print(f"{env.now:8.1f}  Tile {tile_id} DRYER feeds {weight} kg into Silo {idx} "
              f"→ levels: {self.levels[0]:.1f} kg, {self.levels[1]:.1f} kg")

    def consume_for_press(self, env, tile_id, weight, powder_color):
        """Press consumes material from the fuller silo."""
        if powder_color=="red":
            idx =0
        else:
            idx=1    
        if self.levels[idx] >= weight:
            self.levels[idx] -= weight
            print(f"{env.now:8.1f}  Tile {tile_id} PRESS consumes {weight} kg of {powder_color} powder from Silo {idx} "
                  f"→ levels: {self.levels[0]:.1f} kg, {self.levels[1]:.1f} kg")
        else:
            print(f"{env.now:8.1f}  Tile {tile_id} PRESS wants {weight} kg of {powder_color} powder from Silo {idx} "
                  f"but only {self.levels[idx]:.1f} kg left! (levels: "
                  f"{self.levels[0]:.1f}, {self.levels[1]:.1f})")


# -----------------------------
# PROCESS STEPS
# -----------------------------
def milling(env, tile_id, mill_res):
    if env.now > SHIFT_1:
        return #milling is closed after shift 1, so skip milling for this tile
    
    with mill_res.request() as req:
        yield req
        yield env.timeout(MILL_CT)
    print(f"{env.now:8.1f}  Tile {tile_id} milled")


def mixing(env, tile_id, mix_res):
    if env.now > SHIFT_1:
        return #mixing is closed after shift 1, so skip mixing for this tile
    with mix_res.request() as req:
        yield req
        yield env.timeout(MIX_CT)
    print(f"{env.now:8.1f}  Tile {tile_id} mixed")


def drying(env, tile_id, dry_res, silos: SiloSystem):
    if env.now > SHIFT_1:
        return #drying is closed after shift 1, so skip drying for this tile
    with dry_res.request() as req:
        yield req
        yield env.timeout(DRY_CT)

    # Dryer FEEDS silos
    silos.feed_from_dryer(env, tile_id, TILE_WEIGHT_KG)
    print(f"{env.now:8.1f}  Tile {tile_id} dried → fed to silo")


def press(env, tile_id, press_res, silos: SiloSystem):
    with press_res.request() as req:
        yield req

        # Press CONSUMES from silos
        silos.consume_for_press(env, tile_id, TILE_WEIGHT_KG)

        yield env.timeout(PRESS_CT)

    print(f"{env.now:8.1f}  Tile {tile_id} pressed → to glaze")


def glaze(env, tile_id, glaze_res, fifo_after_glaze):
    with glaze_res.request() as req:
        yield req
        yield env.timeout(GLAZE_CT)
    print(f"{env.now:8.1f}  Tile {tile_id} glazed → FIFO before kiln")
    fifo_after_glaze.append(tile_id)
    global tiles_completed, STOP_SIM
    tiles_completed += 1
    if tiles_completed >= NUM_TILES:
        STOP_SIM = True 



def kiln(env, fifo_before_kiln, supermarket, kiln_buffer):
    global supermarket_count

    while not STOP_SIM:
        yield env.timeout(KILN_CT)

        exiting_tile = kiln_buffer.popleft()
        supermarket.append(exiting_tile)
        supermarket_count += 1

        print(f"{env.now:8.1f}  Tile {exiting_tile} exits kiln "
              f"→ Supermarket (Supermarket = {supermarket_count})")

        if fifo_before_kiln:
            entering_tile = fifo_before_kiln.popleft()
            kiln_buffer.append(entering_tile)
        else:
            kiln_buffer.append("EMPTY")
            print(f"{env.now:8.1f}  KILN STARVED — no tile in FIFO")


def packing(env, pack_res, supermarket):
    global supermarket_count, staging_tiles, staging_cases, cases_to_pack

    while not STOP_SIM:
        if cases_to_pack > 0:
            # Need 24 tiles per case
            if len(supermarket) >= 24:
                with pack_res.request() as req:
                    yield req
                    yield env.timeout(PACK_CT)

                # Build 1 case
                for _ in range(24):
                    supermarket.popleft()
                    supermarket_count -= 1

                staging_cases += 1
                cases_to_pack -= 1

                print(f"{env.now:8.1f}  CASE BUILT → STAGING "
                      f"(Staging={staging_cases}, Remaining orders={cases_to_pack})")
            else:
                print(f"{env.now:8.1f}  PACKING WAITING — supermarket empty")
                yield env.timeout(10)

        else:
            # No truck order → packing idle
            yield env.timeout(10)



def truck_pull(env):
    global staging_cases,cases_to_pack

    while not STOP_SIM:
        yield env.timeout(TRUCK_INTERVAL)

        print(f"\n{env.now:8.1f}  TRUCK ARRIVED — needs {TRUCK_CASES} cases")

        if staging_cases >= TRUCK_CASES:
            staging_cases -= TRUCK_CASES
            print(f"{env.now:8.1f}  TRUCK LOADED {TRUCK_CASES} cases "
                  f"(Staging remaining = {staging_cases})\n")
        else:
            print(f"{env.now:8.1f}  STOCKOUT! Only {staging_cases} cases available\n")
            staging_cases = 0
    #   pull signal for packing
        cases_to_pack += TRUCK_CASES
        print(f"{env.now:8.1f}  PACKING ORDER RELEASED: {cases_to_pack} cases needed\n")        

def monitor(env, fifo_before_kiln, supermarket, silos):
    while not STOP_SIM:
        time_log.append(env.now)
        fifo_log.append(len(fifo_before_kiln))
        supermarket_log.append(len(supermarket))
        staging_log.append(staging_cases)
        silo0_log.append(silos.levels[0])
        silo1_log.append(silos.levels[1])
        yield env.timeout(60)   # sample every minute

# -----------------------------
# TILE FLOW
# -----------------------------
def tile_flow(env, tile_id, mill_res, mix_res, dry_res,
              press_res, glaze_res,
              fifo_before_kiln,
              silos: SiloSystem):

    yield env.process(milling(env, tile_id, mill_res))
    yield env.process(mixing(env, tile_id, mix_res))
    yield env.process(drying(env, tile_id, dry_res, silos))   # FEEDS silo
    yield env.process(press(env, tile_id, press_res, silos))  # CONSUMES silo
    yield env.process(glaze(env, tile_id, glaze_res, fifo_before_kiln))


def tile_generator(env, mill_res, mix_res, dry_res,
                   press_res, glaze_res,
                   fifo_before_kiln,
                   silos):
    tile_id = 0
    while env.now < SHIFT_1:   # ← run only during first shift
        env.process(tile_flow(env, tile_id, mill_res, mix_res, dry_res,
                              press_res, glaze_res,
                              fifo_before_kiln,
                              silos))
        tile_id += 1
        yield env.timeout(MILL_CT)






# -----------------------------
# SIMULATION ENVIRONMENT
# -----------------------------
env = simpy.Environment()

mill_res = simpy.Resource(env, capacity=2)
mix_res = simpy.Resource(env, capacity=2)
dry_res = simpy.Resource(env, capacity=2)

press_res = simpy.Resource(env, capacity=1)
glaze_res = simpy.Resource(env, capacity=1)
pack_res = simpy.Resource(env, capacity=1)

fifo_before_kiln = deque(maxlen=FIFO_CAPACITY)

INITIAL_FIFO = FIFO_CAPACITY
for i in range(INITIAL_FIFO):
    fifo_before_kiln.append(f"FIFOInitial-{i}")

supermarket = deque(maxlen=SUPERMARKET_CAPACITY)
INITIAL_SUPERMARKET = 28800
for i in range(INITIAL_SUPERMARKET):
    supermarket.append(i)
supermarket_count = INITIAL_SUPERMARKET

kiln_buffer = deque(maxlen=KILN_CAPACITY)
for i in range(KILN_CAPACITY):
    kiln_buffer.append(f"KilnInitial-{i}")

silos = SiloSystem(INITIAL_SILO_LEVELS)

env.process(tile_generator(env, mill_res, mix_res, dry_res,
                           press_res, glaze_res,
                           fifo_before_kiln,
                           silos))
env.process(kiln(env, fifo_before_kiln, supermarket, kiln_buffer))
env.process(packing(env, pack_res, supermarket))
env.process(truck_pull(env))
env.process(monitor(env, fifo_before_kiln, supermarket, silos))

env.run()

plt.figure(figsize=(14,10))

# FIFO
plt.subplot(2,2,1)
plt.plot(time_log, fifo_log, label="FIFO tiles")
plt.title("FIFO Before Kiln")
plt.xlabel("Time (s)")
plt.ylabel("Tiles")
plt.grid(True)

# Supermarket
plt.subplot(2,2,2)
plt.plot(time_log, supermarket_log, label="Supermarket tiles", color="orange")
plt.title("Supermarket")
plt.xlabel("Time (s)")
plt.ylabel("Tiles")
plt.grid(True)

# Staging
plt.subplot(2,2,3)
plt.plot(time_log, staging_log, label="Staging cases", color="green")
plt.title("Staging Area (Cases)")
plt.xlabel("Time (s)")
plt.ylabel("Cases")
plt.grid(True)

# Silos
plt.subplot(2,2,4)
plt.plot(time_log, silo0_log, label="Silo 0", color="red")
plt.plot(time_log, silo1_log, label="Silo 1", color="purple")
plt.title("Silo Levels (kg)")
plt.xlabel("Time (s)")
plt.ylabel("kg")
plt.legend()
plt.grid(True)

plt.tight_layout()
plt.show()


print("\nFinal silo levels:")
print(f"Silo 0: {silos.levels[0]:.1f} kg")
print(f"Silo 1: {silos.levels[1]:.1f} kg")