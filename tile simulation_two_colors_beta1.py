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
#-----------------------------
# counters for color labels
# and tracking how many of each color are created
#-----------------------------
red_tiles_created = 0
white_tiles_created = 0

WHITE_RATIO = 0.40   # 40% white
RED_RATIO   = 0.60   # 60% red

WHITE_BATCH_SIZE = int(NUM_TILES * WHITE_RATIO)
RED_BATCH_SIZE   = int(NUM_TILES * RED_RATIO)

#-----------------------------
# supermarke color tracking
#-----------------------------  
supermarket_red_count = 0#COUNTER TO TRACK NUMBER OF RED TILES IN SUPERMARKET (for logging purposes)
supermarket_white_count = 0#COUNTER TO TRACK NUMBER OF WHITE TILES IN SUPERMARKET (for logging purposes)

supermarket_red_log = []#LOG TO TRACK NUMBER OF RED TILES IN SUPERMARKET OVER TIME
supermarket_white_log = []#LOG TO TRACK NUMBER OF WHITE TILES IN SUPERMARKET OVER TIME


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

    def feed_from_dryer(self, env, tile_id, weight, color):
        """
        Dryer adds dried material into the correct silo based on COLOR.
        WHITE  → Silo 0
        RED    → Silo 1
        """
        if color == "white":
            idx = 0
        else:  # "red"
            idx = 1

        self.levels[idx] += weight
        print(f"{env.now:8.1f}  Tile {tile_id} ({color}) DRYER feeds {weight} kg into Silo {idx} "
              f"→ levels: {self.levels[0]:.1f} kg, {self.levels[1]:.1f} kg")

    def consume_for_press(self, env, tile_id, weight, color):
        """
        Press consumes material from the correct silo based on COLOR.
        WHITE  → Silo 0
        RED    → Silo 1
        """
        if color == "white":
            idx = 0
        else:  # "red"
            idx = 1

        if self.levels[idx] >= weight:
            self.levels[idx] -= weight
            print(f"{env.now:8.1f}  Tile {tile_id} ({color}) PRESS consumes {weight} kg from Silo {idx} "
                  f"→ levels: {self.levels[0]:.1f} kg, {self.levels[1]:.1f} kg")
        else:
            print(f"{env.now:8.1f}  Tile {tile_id} ({color}) PRESS wants {weight} kg from Silo {idx} "
                  f"but only {self.levels[idx]:.1f} kg left! "
                  f"(levels: {self.levels[0]:.1f}, {self.levels[1]:.1f})")


# -----------------------------
# PROCESS STEPS
# -----------------------------
def milling(env, tile_id, color, mill_res):
    if env.now > SHIFT_1:
        return
    with mill_res.request() as req:
        yield req
        yield env.timeout(MILL_CT)
    print(f"{env.now:8.1f}  Tile {tile_id} ({color}) milled")


def mixing(env, tile_id, color, mix_res):
    if env.now > SHIFT_1:
        return
    with mix_res.request() as req:
        yield req
        yield env.timeout(MIX_CT)
    print(f"{env.now:8.1f}  Tile {tile_id} ({color}) mixed")


def drying(env, tile_id, color, dry_res, silos: SiloSystem):
    if env.now > SHIFT_1:
        return
    with dry_res.request() as req:
        yield req
        yield env.timeout(DRY_CT)

    silos.feed_from_dryer(env, tile_id, TILE_WEIGHT_KG, color)
    print(f"{env.now:8.1f}  Tile {tile_id} ({color}) dried → fed to silo")


def press(env, tile_id, color, press_res, silos: SiloSystem):
    with press_res.request() as req:
        yield req
        
        silos.consume_for_press(env, tile_id, TILE_WEIGHT_KG, color)
        yield env.timeout(PRESS_CT)

    print(f"{env.now:8.1f}  Tile {tile_id} ({color}) pressed → to glaze")


def glaze(env, tile_id, color, glaze_res, fifo_after_glaze):
    global tiles_completed, STOP_SIM
    with glaze_res.request() as req:
        yield req
        yield env.timeout(GLAZE_CT)

    print(f"{env.now:8.1f}  Tile {tile_id} ({color}) glazed → FIFO before kiln")

    # NEW — store (color, tile_id)
    fifo_after_glaze.append((color, tile_id))

    tiles_completed += 1
    if tiles_completed >= NUM_TILES:
        STOP_SIM = True 


def kiln(env, fifo_before_kiln, supermarket, kiln_buffer):
    global supermarket_count
    global supermarket_red_count, supermarket_white_count 

    while not STOP_SIM:
        yield env.timeout(KILN_CT)

        exiting = kiln_buffer.popleft()

        if exiting != "EMPTY":
            color, tile_id = exiting
            supermarket.append((color, tile_id))
            supermarket_count += 1

            # track colors separately
            if color == "red":
                supermarket_red_count += 1
            else:
                supermarket_white_count += 1

            print(f"{env.now:8.1f}  Tile {tile_id} ({color}) exits kiln "
                  f"→ Supermarket (Supermarket = {supermarket_count})")

        else:
            print(f"{env.now:8.1f}  EMPTY slot exits kiln")

        if fifo_before_kiln:
            entering = fifo_before_kiln.popleft()
            kiln_buffer.append(entering)
        else:
            kiln_buffer.append("EMPTY")
            print(f"{env.now:8.1f}  KILN STARVED — no tile in FIFO")


def packing(env, pack_res, supermarket):
    global supermarket_count, staging_tiles, staging_cases, cases_to_pack
    global supermarket_red_count, supermarket_white_count

    while not STOP_SIM:
        if cases_to_pack > 0:
            if len(supermarket) >= 24:
                with pack_res.request() as req:
                    yield req
                    yield env.timeout(PACK_CT)

                for _ in range(24):
                    color, tid = supermarket.popleft()
                    supermarket_count -= 1

                    # decrement color counters
                    if color == "red":
                        supermarket_red_count -= 1
                    else:
                        supermarket_white_count -= 1


                staging_cases += 1
                cases_to_pack -= 1

                print(f"{env.now:8.1f}  CASE BUILT → STAGING "
                      f"(Staging={staging_cases}, Remaining orders={cases_to_pack})")
            else:
                print(f"{env.now:8.1f}  PACKING WAITING — supermarket empty")
                yield env.timeout(10)

        else:
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

        cases_to_pack += TRUCK_CASES
        print(f"{env.now:8.1f}  PACKING ORDER RELEASED: {cases_to_pack} cases needed\n")        


"""def monitor(env, fifo_before_kiln, supermarket, silos):
    while not STOP_SIM:
        time_log.append(env.now)
        fifo_log.append(len(fifo_before_kiln))
        supermarket_red_log.append(supermarket_red_count)
        supermarket_white_log.append(supermarket_white_count)
        staging_log.append(staging_cases)
        silo0_log.append(silos.levels[0])
        silo1_log.append(silos.levels[1])
        yield env.timeout(60)   # sample every minute"""
def monitor(env, fifo_before_kiln, supermarket, silos):
    global supermarket_red_count, supermarket_white_count

    while not STOP_SIM:
        time_log.append(env.now)

        fifo_log.append(len(fifo_before_kiln))

        # Supermarket logs
        supermarket_log.append(len(supermarket))
        supermarket_red_log.append(supermarket_red_count)
        supermarket_white_log.append(supermarket_white_count)

        # Staging log — THIS MUST ALWAYS RUN
        staging_log.append(staging_cases)

        # Silo logs
        silo0_log.append(silos.levels[0])
        silo1_log.append(silos.levels[1])

        yield env.timeout(60)



# -----------------------------
# TILE FLOW
# -----------------------------
def tile_flow(env, tile_id, color,
              mill_res, mix_res, dry_res,
              press_res, glaze_res,
              fifo_before_kiln,
              silos: SiloSystem):

    yield env.process(milling(env, tile_id, color, mill_res))
    yield env.process(mixing(env, tile_id, color, mix_res))
    yield env.process(drying(env, tile_id, color, dry_res, silos))   # FEEDS silo
    yield env.process(press(env, tile_id, color, press_res, silos))  # CONSUMES silo
    yield env.process(glaze(env, tile_id, color, glaze_res, fifo_before_kiln))


# -----------------------------
# TILE GENERATOR
# -----------------------------
"""def tile_generator(env, mill_res, mix_res, dry_res,
                   press_res, glaze_res,
                   fifo_before_kiln,
                   silos):

    global red_tiles_created, white_tiles_created

    tile_id = 0
    while env.now < SHIFT_1:   # ← run only during first shift

        # NEW — alternating color labels
        color = "red" if tile_id % 2 == 0 else "white"
        if color == "red":
            red_tiles_created += 1
        else:
            white_tiles_created += 1

        print(f"{env.now:8.1f}  Tile {tile_id} ({color}) created")

        env.process(tile_flow(env, tile_id, color,
                              mill_res, mix_res, dry_res,
                              press_res, glaze_res,
                              fifo_before_kiln,
                              silos))

        tile_id += 1
        yield env.timeout(MILL_CT)"""
def tile_generator(env, mill_res, mix_res, dry_res,
                   press_res, glaze_res,
                   fifo_before_kiln,
                   silos):

    global red_tiles_created, white_tiles_created

    tile_id = 0

    # Start with white batch 
    current_color = "white"
    remaining_in_batch = WHITE_BATCH_SIZE

    while env.now < SHIFT_1 and tile_id < NUM_TILES:

        # Switch batches when needed
        if remaining_in_batch <= 0:
            if current_color == "white":
                current_color = "red"
                remaining_in_batch = RED_BATCH_SIZE
            else:
                current_color = "white"
                remaining_in_batch = WHITE_BATCH_SIZE

        color = current_color

        # Count tiles
        if color == "red":
            red_tiles_created += 1
        else:
            white_tiles_created += 1

        print(f"{env.now:8.1f}  Tile {tile_id} ({color}) created (batch mode)")

        # Launch tile flow
        env.process(tile_flow(env, tile_id, color,
                              mill_res, mix_res, dry_res,
                              press_res, glaze_res,
                              fifo_before_kiln,
                              silos))

        tile_id += 1
        remaining_in_batch -= 1

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

# FIFO now stores (color, tile_id)
fifo_before_kiln = deque(maxlen=FIFO_CAPACITY)
for i in range(FIFO_CAPACITY):
    fifo_before_kiln.append(("init", f"FIFOInitial-{i}"))

# Supermarket now stores (color, tile_id)
supermarket = deque(maxlen=SUPERMARKET_CAPACITY)
for i in range(28800):
    supermarket.append(("init", i))
supermarket_count = 28800

# Kiln buffer now stores (color, tile_id)
kiln_buffer = deque(maxlen=KILN_CAPACITY)
for i in range(KILN_CAPACITY):
    kiln_buffer.append(("init", f"KilnInitial-{i}"))

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

# -----------------------------
# PLOTTING RESULTS
# -----------------------------
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
plt.plot(time_log, supermarket_log, label="Total", color="black")
plt.plot(time_log, supermarket_red_log, label="Red", color="red")
plt.plot(time_log, supermarket_white_log, label="White", color="blue")
plt.title("Supermarket Inventory by Color")
plt.xlabel("Time (s)")
plt.ylabel("Tiles")
plt.legend()
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

print(f"\nTiles created: red={red_tiles_created}, white={white_tiles_created}")
