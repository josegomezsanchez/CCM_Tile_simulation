import simpy
from collections import deque
import math
import matplotlib.pyplot as plt

# -----------------------------
# PARAMETERS
# -----------------------------
CASES_PER_YEAR = 240000
WORKING_DAY_PER_YEAR = 240
CASES_PER_DAY = int(CASES_PER_YEAR / WORKING_DAY_PER_YEAR)
TILES_PER_CASE = 24
TILES_PER_DAY = CASES_PER_DAY * TILES_PER_CASE
PRESS_NUMBER_OF_SHIFTS = 3
MILL_NUMBER_OF_SHIFTS = 1
MILLING_TILES_PER_SHIFT = TILES_PER_DAY / MILL_NUMBER_OF_SHIFTS
TILE_WEIGHT_KG = 0.375
SHIFT_1 = 8 * 60 * 60
SHIFT_3 = 24 * 60 * 60
POWDER_PER_SHIFT_KG = MILLING_TILES_PER_SHIFT * TILE_WEIGHT_KG
POWDER_FLOW_RATE = POWDER_PER_SHIFT_KG / SHIFT_1
RESIDENCE_TIME_MIXER = 5 * 60  # seconds
HOPPER_SIZE = POWDER_FLOW_RATE * RESIDENCE_TIME_MIXER

# PRESS AND GLAZE CT
PRESS_CT = (PRESS_NUMBER_OF_SHIFTS * SHIFT_1) / TILES_PER_DAY
GLAZE_CT = PRESS_CT

# MILL / MIX / DRY
MILL_CT = 0.9
MIX_CT = TILE_WEIGHT_KG / POWDER_FLOW_RATE
DRY_CT = MIX_CT

# KILN DATA
KILN_RESIDENCE = 14 * 60 * 60
KILN_CT = PRESS_CT
KILN_CAPACITY = math.ceil(KILN_RESIDENCE / KILN_CT)

PACK_CT = 24.0

FIFO_CAPACITY = 240
SUPERMARKET_CAPACITY = 28800

NUM_TILES = 8000 * 3

# SILO CAPACITY
SILO_CAPACITY_IN_DAYS = 1.2
SILO_CAPACITY_KG = (
    POWDER_FLOW_RATE * MILL_NUMBER_OF_SHIFTS * SHIFT_1 * SILO_CAPACITY_IN_DAYS
)
INITIAL_SILO_LEVELS = [SILO_CAPACITY_KG, SILO_CAPACITY_KG]

supermarket_count = 0

CUSTOMER_PULL_CT = 5.0

finished_tiles = 0
finished_cases = 0

TRUCK_CAPACITY = 3000
TRUCK_CASES = math.ceil(TRUCK_CAPACITY / TILE_WEIGHT_KG / TILES_PER_CASE)
TRUCKS_PER_DAY = 3
TRUCK_INTERVAL = 24 * 60 * 60 / TRUCKS_PER_DAY

tiles_completed = 0
cases_to_pack = 0

# -----------------------------
# COLOR CREATION COUNTERS
# -----------------------------
red_tiles_created = 0
white_tiles_created = 0

WHITE_RATIO = 0.40
RED_RATIO = 0.60

WHITE_BATCH_SIZE = int(NUM_TILES * WHITE_RATIO)
RED_BATCH_SIZE = int(NUM_TILES * RED_RATIO)

# -----------------------------
# SUPERMARKET INITIALIZATION
# -----------------------------
supermarket_red_count = 0
supermarket_white_count = 0

supermarket_red_log = []
supermarket_white_log = []

INITIAL_SUPERMARKET_RED = int(TILES_PER_DAY * RED_RATIO)
INITIAL_SUPERMARKET_WHITE = int(TILES_PER_DAY * WHITE_RATIO)

# -----------------------------
# PACKED / STAGING COLOR TRACKING
# -----------------------------
packed_red_cases = 0
packed_white_cases = 0

staging_red_cases = 0
staging_white_cases = 0

INITIAL_STAGING_RED = int(CASES_PER_DAY*RED_RATIO)
INITIAL_STAGING_WHITE = int(CASES_PER_DAY*WHITE_RATIO)

# -----------------------------------------
# CAPACITY SETTINGS
# -----------------------------------------
STAGING_CAPACITY = CASES_PER_DAY   # or whatever your real capacity is


# -----------------------------
# DATA COLLECTION FOR PLOTTING
# -----------------------------
time_log = []
fifo_log = []
supermarket_log = []
silo0_log = []
silo1_log = []
case_colors = []
staging_red_log = []
staging_white_log = []

STOP_SIM = False

# -----------------------------
# SILO SYSTEM
# -----------------------------
class SiloSystem:
    def __init__(self, initial_levels):
        self.levels = initial_levels[:]

    def feed_from_dryer(self, env, tile_id, weight, color):
        if color == "white":
            idx = 0
        else:
            idx = 1

        self.levels[idx] += weight
        print(
            f"{env.now:8.1f}  Tile {tile_id} ({color}) DRYER feeds {weight} kg into Silo {idx} "
            f"→ levels: {self.levels[0]:.1f} kg, {self.levels[1]:.1f} kg"
        )

    def consume_for_press(self, env, tile_id, weight, color):
        if color == "white":
            idx = 0
        else:
            idx = 1

        if self.levels[idx] >= weight:
            self.levels[idx] -= weight
            print(
                f"{env.now:8.1f}  Tile {tile_id} ({color}) PRESS consumes {weight} kg from Silo {idx} "
                f"→ levels: {self.levels[0]:.1f} kg, {self.levels[1]:.1f} kg"
            )
        else:
            print(
                f"{env.now:8.1f}  Tile {tile_id} ({color}) PRESS wants {weight} kg from Silo {idx} "
                f"but only {self.levels[idx]:.1f} kg left! "
                f"(levels: {self.levels[0]:.1f}, {self.levels[1]:.1f})"
            )


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


def glaze(env, tile_id, color, glaze_res, fifo_before_kiln):
    global tiles_completed, STOP_SIM
    with glaze_res.request() as req:
        yield req
        yield env.timeout(GLAZE_CT)

    print(f"{env.now:8.1f}  Tile {tile_id} ({color}) glazed → FIFO before kiln")

    fifo_before_kiln.append((color, tile_id))

    tiles_completed += 1
    if tiles_completed >= NUM_TILES:
        STOP_SIM = True


# -----------------------------
# KILN PROCESS
# -----------------------------
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

            if color == "red":
                supermarket_red_count += 1
            else:
                supermarket_white_count += 1

            print(
                f"{env.now:8.1f}  Tile {tile_id} ({color}) exits kiln "
                f"→ Supermarket (Supermarket = {supermarket_count})"
            )
        else:
            print(f"{env.now:8.1f}  EMPTY slot exits kiln")

        if fifo_before_kiln:
            entering = fifo_before_kiln.popleft()
            kiln_buffer.append(entering)
        else:
            kiln_buffer.append("EMPTY")
            print(f"{env.now:8.1f}  KILN STARVED — no tile in FIFO")


# -----------------------------
# PACKING PROCESS (PURE CASES ONLY)
# -----------------------------
def packing(env, pack_res, supermarket):
    global supermarket_count, cases_to_pack
    global supermarket_red_count, supermarket_white_count
    global packed_red_cases, packed_white_cases
    global staging_red_cases, staging_white_cases

    while not STOP_SIM:
        if cases_to_pack > 0:

            reds_available = sum(1 for c, _ in supermarket if c == "red")
            whites_available = sum(1 for c, _ in supermarket if c == "white")

            if reds_available >= 24:
                target_color = "red"
            elif whites_available >= 24:
                target_color = "white"
            else:
                print(
                    f"{env.now:8.1f}  PACKING WAITING — insufficient tiles for pure case"
                )
                yield env.timeout(10)
                continue

            with pack_res.request() as req:
                yield req
                yield env.timeout(PACK_CT)

            case_colors_local = []
            tiles_removed = 0

            new_supermarket = deque()
            while supermarket and tiles_removed < 24:
                color, tid = supermarket.popleft()
                if color == target_color:
                    case_colors_local.append(color)
                    supermarket_count -= 1
                    if color == "red":
                        supermarket_red_count -= 1
                    else:
                        supermarket_white_count -= 1
                    tiles_removed += 1
                else:
                    new_supermarket.append((color, tid))

            while supermarket:
                new_supermarket.append(supermarket.popleft())
            supermarket.extend(new_supermarket)

            if target_color == "red":
                packed_red_cases += 1
                staging_red_cases += 1
                case_color = "RED"
            else:
                packed_white_cases += 1
                staging_white_cases += 1
                case_color = "WHITE"

            cases_to_pack -= 1

            print(
                f"{env.now:8.1f}  CASE BUILT ({case_color}) → STAGING "
                f"(Remaining orders={cases_to_pack})"
            )

        else:
            yield env.timeout(10)


# -----------------------------
# TRUCK PULL PROCESS (COLOR-ONLY STAGING)
# -----------------------------
def truck_pull(env):
    global cases_to_pack
    global staging_red_cases, staging_white_cases

    while not STOP_SIM:
        yield env.timeout(TRUCK_INTERVAL)

        print(f"\n{env.now:8.1f}  TRUCK ARRIVED — needs {TRUCK_CASES} cases")

        cases_needed = TRUCK_CASES

        red_to_pull = min(staging_red_cases, cases_needed)
        staging_red_cases -= red_to_pull
        cases_needed -= red_to_pull

        white_to_pull = min(staging_white_cases, cases_needed)
        staging_white_cases -= white_to_pull
        cases_needed -= white_to_pull

        pulled_total = red_to_pull + white_to_pull

        if pulled_total < TRUCK_CASES:
            print(f"{env.now:8.1f}  STOCKOUT! Only {pulled_total} cases available\n")
        else:
            print(f"{env.now:8.1f}  TRUCK LOADED {TRUCK_CASES} cases\n")

        cases_to_pack += TRUCK_CASES
        print(
            f"{env.now:8.1f}  PACKING ORDER RELEASED: {cases_to_pack} cases needed\n"
        )


# -----------------------------
# MONITORING PROCESS
# -----------------------------
def monitor(env, fifo_before_kiln, supermarket, silos):
    global supermarket_red_count, supermarket_white_count
    global staging_red_cases, staging_white_cases

    while not STOP_SIM:
        time_log.append(env.now)

        fifo_log.append(len(fifo_before_kiln))

        supermarket_log.append(len(supermarket))
        supermarket_red_log.append(supermarket_red_count)
        supermarket_white_log.append(supermarket_white_count)

        staging_red_log.append(staging_red_cases)
        staging_white_log.append(staging_white_cases)

        silo0_log.append(silos.levels[0])
        silo1_log.append(silos.levels[1])

        yield env.timeout(60)


# -----------------------------
# TILE FLOW
# -----------------------------
def tile_flow(
    env,
    tile_id,
    color,
    mill_res,
    mix_res,
    dry_res,
    press_res,
    glaze_res,
    fifo_before_kiln,
    silos: SiloSystem,
):
    yield env.process(milling(env, tile_id, color, mill_res))
    yield env.process(mixing(env, tile_id, color, mix_res))
    yield env.process(drying(env, tile_id, color, dry_res, silos))
    yield env.process(press(env, tile_id, color, press_res, silos))
    yield env.process(glaze(env, tile_id, color, glaze_res, fifo_before_kiln))


# -----------------------------
# TILE GENERATOR
# -----------------------------
def tile_generator(
    env,
    mill_res,
    mix_res,
    dry_res,
    press_res,
    glaze_res,
    fifo_before_kiln,
    silos,
):
    global red_tiles_created, white_tiles_created

    tile_id = 0

    current_color = "white"
    remaining_in_batch = WHITE_BATCH_SIZE

    while env.now < SHIFT_1 and tile_id < NUM_TILES:

        if remaining_in_batch <= 0:
            if current_color == "white":
                current_color = "red"
                remaining_in_batch = RED_BATCH_SIZE
            else:
                current_color = "white"
                remaining_in_batch = WHITE_BATCH_SIZE

        color = current_color

        if color == "red":
            red_tiles_created += 1
        else:
            white_tiles_created += 1

        print(f"{env.now:8.1f}  Tile {tile_id} ({color}) created (batch mode)")

        env.process(
            tile_flow(
                env,
                tile_id,
                color,
                mill_res,
                mix_res,
                dry_res,
                press_res,
                glaze_res,
                fifo_before_kiln,
                silos,
            )
        )

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

# FIFO preloaded with RED tiles
fifo_before_kiln = deque(maxlen=FIFO_CAPACITY)
for i in range(FIFO_CAPACITY):
    fifo_before_kiln.append(("red", f"FIFOStart-{i}"))

# Supermarket starts empty
supermarket = deque(maxlen=SUPERMARKET_CAPACITY)
supermarket_red_count = 0
supermarket_white_count = 0

# -----------------------------------------
# PRELOAD SUPERMARKET WITH INITIAL INVENTORY
# -----------------------------------------

# Load red cases
for i in range(INITIAL_SUPERMARKET_RED):
    supermarket.append(("red", f"InitR-{i}"))
supermarket_red_count = INITIAL_SUPERMARKET_RED

# Load white cases
for i in range(INITIAL_SUPERMARKET_WHITE):
    supermarket.append(("white", f"InitW-{i}"))
supermarket_white_count = INITIAL_SUPERMARKET_WHITE

print(f"Supermarket initialized with {supermarket_red_count} red and {supermarket_white_count} white cases")

#------------------------------
#PRELOAD STAGING WITH INITIAL INVENTORY
#------------------------------
staging_red = deque(maxlen=STAGING_CAPACITY)
staging_white = deque(maxlen=STAGING_CAPACITY)

staging_red_cases = 0
staging_white_cases = 0

# -----------------------------------------
# PRELOAD STAGING
# -----------------------------------------

for i in range(INITIAL_STAGING_RED):
    staging_red.append(f"InitSR-{i}")
staging_red_cases = INITIAL_STAGING_RED

for i in range(INITIAL_STAGING_WHITE):
    staging_white.append(f"InitSW-{i}")
staging_white_cases = INITIAL_STAGING_WHITE

print(f"Staging initialized with {staging_red_cases} red and {staging_white_cases} white cases")
print("DEBUG — Initial staging:",
      "red =", staging_red_cases,
      "white =", staging_white_cases,
      "total =", staging_red_cases + staging_white_cases)


# Kiln buffer preloaded with RED tiles
kiln_buffer = deque(maxlen=KILN_CAPACITY)
for i in range(KILN_CAPACITY):
    kiln_buffer.append(("red", f"KilnStart-{i}"))

silos = SiloSystem(INITIAL_SILO_LEVELS)

env.process(
    tile_generator(
        env,
        mill_res,
        mix_res,
        dry_res,
        press_res,
        glaze_res,
        fifo_before_kiln,
        silos,
    )
)
env.process(kiln(env, fifo_before_kiln, supermarket, kiln_buffer))
env.process(packing(env, pack_res, supermarket))
env.process(truck_pull(env))
env.process(monitor(env, fifo_before_kiln, supermarket, silos))

env.run()

total_staging = staging_red_cases + staging_white_cases
print(
    "Staging now:",
    "total =",
    total_staging,
    "red =",
    staging_red_cases,
    "white =",
    staging_white_cases,
)

# -----------------------------
# PLOTTING RESULTS
# -----------------------------
plt.figure(figsize=(14, 10))

# FIFO
plt.subplot(2, 2, 1)
plt.plot(time_log, fifo_log, label="FIFO tiles")
plt.title("FIFO Before Kiln")
plt.xlabel("Time (s)")
plt.ylabel("Tiles")
plt.grid(True)

# Supermarket
plt.subplot(2, 2, 2)
plt.plot(time_log, supermarket_log, label="Total", color="black")
plt.plot(time_log, supermarket_red_log, label="Red", color="red")
plt.plot(time_log, supermarket_white_log, label="White", color="blue")
plt.title("Supermarket Inventory by Color")
plt.xlabel("Time (s)")
plt.ylabel("Tiles")
plt.legend()
plt.grid(True)

# Staging (color-only)
plt.subplot(2, 2, 3)
plt.plot(time_log, staging_red_log, label="Red cases", color="red")
plt.plot(time_log, staging_white_log, label="White cases", color="blue")
plt.title("Staging Area Cases by Color")
plt.xlabel("Time (s)")
plt.ylabel("Cases")
plt.legend()
plt.grid(True)

# Silos
plt.subplot(2, 2, 4)
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
