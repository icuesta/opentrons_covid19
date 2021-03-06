from opentrons.types import Point
from opentrons.drivers.rpi_drivers import gpio
from opentrons import protocol_api
import os
import json
import math
import time

# metadata
metadata = {
    'protocolName': 'S3 Station B Version 2',
    'author': 'Nick <protocols@opentrons.com> Sara <smonzon@isciii.es> Miguel <mjuliam@isciii.es>',
    'source': 'Custom Protocol Request',
    'apiLevel': '2.3'
}

"""
REAGENT SETUP:
- slot 7 12-channel reservoir:
    - elution buffer: channel 1
    - magnetic beads: channel 2
    - bead buffer: channels 3-5
    - wash 1: channels 6-7
    - wash 2: channels 8-9
    - wash 3: channels 10-11
- slot 11 single-channel reservoir:
    - empty reservoir for liquid waste (supernatant removals)
"""

# Parameters to adapt the protocol
NUM_SAMPLES = 8
REAGENT_LABWARE = 'nest 12 reservoir plate'
MAGPLATE_LABWARE = 'nest deep generic well plate'
WASTE_LABWARE = 'nest 1 reservoir plate'
ELUTION_LABWARE = 'opentrons aluminum nest plate'
TIP_TRACK = True
DISPENSE_BEADS = False

## global vars
robot = None
tip_log = {}
tip_log['count'] = {}
tip_log['tips'] = {}
tip_log['max'] = {}

"""
NUM_SAMPLES is the number of samples, must be an integer number

REAGENT_LABWARE must be one of the following:
    nest 12 reservoir plate

MAGPLATE_LABWARE must be one of the following:
    opentrons deep generic well plate
    nest deep generic well plate
    vwr deep generic well plate

WASTE labware
    nest 1 reservoir plate

ELUTION_LABWARE
    opentrons aluminum biorad plate
    opentrons aluminum nest plate
"""
# Parameters to adapt the protocol
# Warning writing any Parameters below this line.
# It will be deleted if opentronsWeb is used.

# End Parameters to adapt the protocol

# Constants
REAGENT_LW_DICT = {
    'nest 12 reservoir plate': 'nest_12_reservoir_15ml'
}

MAGPLATE_LW_DICT = {
    'opentrons deep generic well plate': 'usascientific_96_wellplate_2.4ml_deep',
    'nest deep generic well plate': 'nest_96_deepwellplate_2000ul',
    'vwr deep generic well plate': 'vwr_96_deepwellplate_2000ul'
}

WASTE_LW_DICT = {
    # Radius of each possible tube
    'nest 1 reservoir plate': 'nest_1_reservoir_195ml'
}

ELUTION_LW_DICT = {
    'opentrons aluminum biorad plate': 'opentrons_96_aluminumblock_biorad_wellplate_200ul',
    'opentrons aluminum nest plate': 'opentrons_96_aluminumblock_nest_wellplate_100ul'

}

# Function definitions
def check_door():
    return gpio.read_window_switches()

def confirm_door_is_closed():
    #Check if door is opened
    if check_door() == False:
        #Set light color to red and pause
        gpio.set_button_light(1,0,0)
        robot.pause(f"Please, close the door")
        time.sleep(3)
        confirm_door_is_closed()
    else:
        #Set light color to green
        gpio.set_button_light(0,1,0)

def finish_run():
    #Set light color to blue
    gpio.set_button_light(0,0,1)


def retrieve_tip_info(pip,tipracks,file_path = '/data/B/tip_log.json'):
    global tip_log
    if not tip_log['count'] or pip not in tip_log['count']:
        if not robot.is_simulating():
            if os.path.isfile(file_path):
                with open(file_path) as json_file:
                    data = json.load(json_file)
                    if 'tips1000' in data:
                        tip_log['count'][pip] = data['tips1000']
                    elif 'tips300' in data:
                        tip_log['count'][pip] = data['tips300']
                    else:
                        tip_log['count'][pip] = 0
            else:
                tip_log['count'][pip] = 0
        else:
            tip_log['count'][pip] = 0

        if "8-Channel" in str(pip):
            tip_log['tips'][pip] =  [tip for rack in tipracks for tip in rack.rows()[0]]
        else:
            tip_log['tips'][pip] = [tip for rack in tipracks for tip in rack.wells()]

        tip_log['max'][pip] = len(tip_log['tips'][pip])

    return tip_log

def save_tip_info(pip, file_path = '/data/B/tip_log.json'):
    if not robot.is_simulating():
        if "P1000" in str(pip):
            data = {'tips1000': tip_log['count'][pip]}
        elif "P300" in str(pip):
            data = {'tips300': tip_log['count'][pip]}

        with open(file_path, 'w') as outfile:
            json.dump(data, outfile)

def pick_up(pip,tiprack):
    ## retrieve tip_log
    global tip_log
    if not tip_log:
        tip_log = {}
    tip_log = retrieve_tip_info(pip,tiprack)
    if tip_log['count'][pip] == tip_log['max'][pip]:
        robot.pause('Replace ' + str(pip.max_volume) + 'µl tipracks before \
resuming.')
        pip.reset_tipracks()
        tip_log['count'][pip] = 0
    pip.pick_up_tip(tip_log['tips'][pip][tip_log['count'][pip]])
    tip_log['count'][pip] += 1

def mix_beads(reps, dests, pip, tiprack):
    ## Dispense beads to deep well plate.
    for i, m in enumerate(dests):
        if not pip.hw_pipette['has_tip']:
            pick_up(pip,tiprack)
        for i in range(reps):
            pip.aspirate(200, m.bottom(2))
            pip.dispense(200, m.bottom(2), rate=2)
        # PENDING TO FIX THIS blow_out
        pip.blow_out(m.top(-2))
        pip.aspirate(20, m.top(-2))
        pip.drop_tip(home_after=False)

def dispense_beads(sources,dests,pip,tiprack):
    ## Mix beads prior to dispensing.
    pick_up(pip,tiprack)
    for s in sources:
        for _ in range(5):
            pip.aspirate(200, s.bottom(20))
            pip.dispense(200, s.bottom(20))

    ## Dispense beads to deep well plate.
    for i, m in enumerate(dests):
        if not pip.hw_pipette['has_tip']:
            pick_up(pip,tiprack)
        pip.transfer(200, dests[i//3], m.bottom(5), new_tip='never', air_gap=20)
        pip.blow_out(m.top(-2))
        pip.drop_tip(home_after=False)
        pick_up(pip,tiprack)
        pip.transfer(200, dests[i//3], m.bottom(5), new_tip='never', air_gap=20)
        mix_beads(7, dests, pip, tiprack)

def remove_supernatant(sources,waste,pip,tiprack):
    for i, m in enumerate(sources):
        # side = -1 if (i % 8) % 2 == 0 else 1
        # loc = m.bottom(0.5).move(Point(x=side*2))
        loc = m.bottom(1)
        pick_up(pip,tiprack)
        pip.move_to(m.center())
        pip.transfer(800, loc, waste, air_gap=100, new_tip='never')
        pip.blow_out(waste)
        pip.drop_tip(home_after=False)

def wash(wash_sets,dests,waste,magdeck,pip,tiprack):
    for wash_set in wash_sets:
        for i, m in enumerate(dests):
            # transfer and mix wash with beads
            magdeck.disengage()
            wash_chan = wash_set[i//6]
            side = 1 if i % 2 == 0 else -1
            #disp_loc = m.bottom(1).move(Point(x=side*2))
            #asp_loc = m.bottom(1).move(Point(x=-1*side*2))
            asp_loc = m.bottom(1)
            # disp_loc = m.bottom(2).move(Point(x=side*2))
            disp_loc = m.bottom(5)
            pick_up(pip,tiprack)
            pip.transfer(
                200, wash_chan.bottom(2), m.center(), new_tip='never', air_gap=20)
            pip.mix(7, 175, disp_loc)
            pip.move_to(m.top(-20))

            magdeck.engage(height_from_base=22)
            robot.delay(seconds=75, msg='Incubating on magnet for 75 seconds.')

            # remove supernatant
            aspire_default_speed = pip.flow_rate.aspirate
            pip.flow_rate.aspirate = 75
            pip.transfer(200, asp_loc, waste, new_tip='never', air_gap=20)
            pip.flow_rate.aspirate = aspire_default_speed
            pip.blow_out(waste)
            pip.drop_tip(home_after=False)

def elute_samples(sources,dests,buffer,magdeck,pip,tipracks):
    ## dispense buffer
    for i, m in enumerate(sources):
        #side = 1 if i % 2 == 0 else -1
        #disp_loc = m.bottom(5).move(Point(x=side*2))
        pick_up(pip,tipracks)
        dispense_default_speed = pip.flow_rate.dispense
        pip.flow_rate.dispense = 1500
        pip.transfer(
            50, buffer, m.bottom(1), new_tip='never', air_gap=10)
        pip.mix(20, 40, m.bottom(1))
        pip.flow_rate.dispense = dispense_default_speed
        pip.drop_tip(home_after=False)

    ## Incubation steps
    robot.delay(minutes=5, msg='Incubating off magnet for 5 minutes.')
    magdeck.engage(height_from_base=22)
    robot.delay(seconds=120, msg='Incubating on magnet for 60 seconds.')

    aspire_default_speed = pip.flow_rate.aspirate
    pip.flow_rate.aspirate = 50
    ## Dispense elutes in pcr plate.
    for i, (m, e) in enumerate(zip(sources, dests)):
        # tranfser and mix elution buffer with beads
        side = 1 if i % 2 == 0 else -1
        asp_loc = m.bottom(5).move(Point(x=-1*side*2))
        pick_up(pip,tipracks)
        # transfer elution to new plate
        pip.transfer(40, asp_loc, e, new_tip='never', air_gap=10)
        pip.blow_out(e.top(-2))
        pip.drop_tip(home_after=False)
    pip.flow_rate.aspirate = aspire_default_speed

def run(ctx: protocol_api.ProtocolContext):
    global robot
    robot = ctx

    # confirm door is close
    if not ctx.is_simulating():
        confirm_door_is_closed()

    # load labware and modules
    ## ELUTION LABWARE
    if ELUTION_LABWARE not in ELUTION_LW_DICT:
        raise Exception('Invalid ELUTION_LABWARE. Must be one of the \
    following:\nopentrons aluminum biorad plate\nopentrons aluminum nest plate')

    elution_plate = ctx.load_labware(
        ELUTION_LW_DICT[ELUTION_LABWARE], '1',
        'elution plate')

    ## MAGNETIC PLATE LABWARE
    magdeck = ctx.load_module('magdeck', '10')
    magdeck.disengage()

    if MAGPLATE_LABWARE not in MAGPLATE_LW_DICT:
        raise Exception('Invalid MAGPLATE_LABWARE. Must be one of the \
following:\nopentrons deep generic well plate\nnest deep generic well plate\nvwr deep generic well plate')

    magplate = magdeck.load_labware(MAGPLATE_LW_DICT[MAGPLATE_LABWARE])

    ## WASTE LABWARE
    if WASTE_LABWARE not in WASTE_LW_DICT:
        raise Exception('Invalid WASTE_LABWARE. Must be one of the \
    following:\nnest 1 reservoir plate')

    waste = ctx.load_labware(
        WASTE_LW_DICT[WASTE_LABWARE], '11', 'waste reservoir').wells()[0].top(-10)

    ## REAGENT RESERVOIR
    if REAGENT_LABWARE not in REAGENT_LW_DICT:
        raise Exception('Invalid REAGENT_LABWARE. Must be one of the \
    following:\nnest 12 reservoir plate')

    reagent_res = ctx.load_labware(
        REAGENT_LW_DICT[REAGENT_LABWARE], '7', 'reagent reservoir')

    ## TIPS
    # using standard tip definition despite actually using filter tips
    # so that the tips can accommodate ~220µl per transfer for efficiency
    tips300 = [
        ctx.load_labware(
            'opentrons_96_tiprack_300ul', slot, '200µl filter tiprack')
        for slot in ['2', '3', '5', '6', '9','4']
    ]
    tips1000 = [
        ctx.load_labware('opentrons_96_filtertiprack_1000ul', slot,
                         '1000µl filter tiprack')
        for slot in ['8']
    ]

    # reagents and samples
    num_cols = math.ceil(NUM_SAMPLES/8)
    mag_samples_m = magplate.rows()[0][:num_cols]
    mag_samples_s = magplate.wells()[:NUM_SAMPLES]
    elution_samples_m = elution_plate.rows()[0][:num_cols]
    elution_buffer = reagent_res.wells()[0]
    bead_buffer = reagent_res.wells()[1:5]
    wash_sets = [reagent_res.wells()[i:i+2] for i in [5, 7, 9]]

    # pipettes
    m300 = ctx.load_instrument('p300_multi_gen2', 'left', tip_racks=tips300)
    p1000 = ctx.load_instrument('p1000_single_gen2', 'right',
                                tip_racks=tips1000)

    m300.flow_rate.aspirate = 150
    m300.flow_rate.dispense = 300
    m300.flow_rate.blow_out = 300
    p1000.flow_rate.aspirate = 100
    p1000.flow_rate.dispense = 1000
    p1000.flow_rate.blow_out = 1000

    if(DISPENSE_BEADS):
        # premix, transfer, and mix magnetic beads with sample
        ## bead dests depending on number of samples
        bead_dests = bead_buffer[:math.ceil(num_cols/4)]
        dispense_beads(bead_dests,mag_samples_m,m300,tips300)
    else:
        # Mix bead
        mix_beads(7, mag_samples_m,m300,tips300)

    # incubate off and on magnet
    ctx.delay(minutes=5, msg='Incubating off magnet for 5 minutes.')

    ## First incubate on magnet.
    magdeck.engage(height_from_base=22)
    ctx.delay(minutes=5, msg='Incubating on magnet for 5 minutes.')

    # remove supernatant with P1000
    remove_supernatant(mag_samples_s,waste,p1000,tips1000)

    # 3x washes
    wash(wash_sets,mag_samples_m,waste,magdeck,m300,tips300)

    # Airdrying -> REMOVE THIS STEP
    #ctx.delay(minutes=5, msg='Airdrying for 5 minutes.')

    # elute samples
    elute_samples(mag_samples_m,elution_samples_m,elution_buffer,magdeck,m300,tips300)

    # track final used tip
    save_tip_info(p1000)
    save_tip_info(m300)

    finish_run()
