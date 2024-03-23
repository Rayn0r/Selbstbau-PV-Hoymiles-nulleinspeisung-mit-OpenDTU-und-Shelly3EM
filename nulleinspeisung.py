#!/usr/bin/env python3
"""
Ein Python-Skript, das den aktuellen Hausverbrauch aus einem Shelly 3EM (Pro) ausliest,
die Nulleinspeisung berechnet und die Ausgangsleistung eines Hoymiles-Wechselrichters
mit Hilfe der OpenDTU entsprechend anpasst.
Somit wird kein unnötiger Strom ins Betreibernetz abgegeben.
"""

import time
import sys
import json
import os
import configparser
from requests.auth import HTTPBasicAuth
import requests

CONFIG_FILE_NAME = 'null_config.ini'
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = f'{WORK_DIR}/{CONFIG_FILE_NAME}'

check_file = os.path.isfile(CONFIG_FILE)

if not check_file:
    print(f"""
    Please rename supplied {CONFIG_FILE_NAME}.template to {CONFIG_FILE_NAME}
    and place it into {WORK_DIR}/.
    Then edit its content according to your setup.
    """)
    sys.exit(1)

config = configparser.ConfigParser()
config.read(CONFIG_FILE)

# Diese Daten müssen angepasst werden:
SERIAL = config['Inverter1']['SERIAL'] # Seriennummer des Hoymiles Wechselrichters
MAXIMUM_WR = config['Inverter1'].getint('MAXIMUM_WR') # Maximale Ausgabe des Wechselrichters
MINIMUM_WR = config['Inverter1'].getint('MINIMUM_WR') # Minimale Ausgabe des Wechselrichters

DTU_IP =   config['Inverter1']['DTU_IP'] # IP Adresse von OpenDTU
DTU_NUTZER = config['Inverter1']['DTU_NUTZER'] # OpenDTU Nutzername
DTU_PASSWORT = config['Inverter1']['DTU_PASSWORT'] # OpenDTU Passwort

SHELLY_IP = config['Inverter1']['SHELLY_IP'] # IP Adresse von Shelly 3EM

STANDARD_TIMEOUT = config['Inverter1'].getint('STANDARD_TIMEOUT') # Zeit in Sekunden
EM3PRO = config['Inverter1'].getboolean('EM3PRO') # Wird ein Shelly EM3 Pro eingesetzt?

def get_request(url_name, header_definition=''):
    """
    Retrieve simple value from URL and return it
    in case things go wrong, return False
    """
    try:
        daten = requests.get(url = url_name, timeout=STANDARD_TIMEOUT,
                headers=header_definition ).json()
        return daten
    except Exception as fehler: # pylint: disable=broad-except
        print(f"Fehler {fehler} beim Aufruf von {url_name}")
        return False

def post_request(url_name, post_daten, auth_daten='', header_definition=''):
    """
    send post request to url_name and return the request status
    if things go wrong, return False
    """
    try:
        daten = requests.post(url=url_name, data=post_daten,
                    timeout=STANDARD_TIMEOUT, auth=auth_daten, headers=header_definition).json()
        return daten
    except Exception as fehler: # pylint: disable=broad-except
        print(f"Fehler {fehler} beim Aufruf von {url_name}")
        return False

while True:
    # Nimmt Daten von der openDTU Rest-API und übersetzt sie in ein json-Format
    r_dtu = get_request(f'http://{DTU_IP}/api/livedata/status')

    # hole den git tag der aktuellen Firmware aus /api/livedata/status
    system_status = get_request(f'http://{DTU_IP}/api/system/status')
    if system_status:
        # hole git tag
        git_tag = system_status['git_hash'][1:]
        git_tag_arr = git_tag.split('.')
        git_tag_arr_i = list(map(int, git_tag_arr))

    if r_dtu and system_status:
        #print(f"r_dtu:\n{json.dumps(r_dtu, indent=2)}", flush=True)
        # Selektiert spezifische Daten aus der json response
        REACHABLE   = r_dtu['inverters'][0]['reachable'] # Ist DTU erreichbar?
        producing   = int(r_dtu['inverters'][0]['producing']) # Produziert der Wechselrichter etwas?
        altes_limit = int(r_dtu['inverters'][0]['limit_absolute']) # Altes Limit

        # das Format von /api/livedata/status hat sich mit v24.2.12 geändert...
        # https://github.com/tbnobody/OpenDTU/releases/tag/v24.2.12
        if git_tag_arr_i[0] >= 24 and git_tag_arr_i[1] >= 2 and git_tag_arr_i[2] >= 12:
            power   = r_dtu['total']['Power']['v'] # Abgabe BKW AC in Watt
        else:
            power   = r_dtu['inverters'][0]['AC']['0']['Power']['v'] # Abgabe BKW AC in Watt
        # print(f"{REACHABLE} {producing} {altes_limit} {power}")
    else:
        print("Konnte keine Daten von OpenDTU empfangen.")
        time.sleep(STANDARD_TIMEOUT) # warte STANDARD_TIMEOUT bevor wir es erneut probieren.
        continue

    # Nimmt Daten von der Shelly 3EM (Pro) Rest-API(2) und übersetzt sie ins json-Format
    url_api1 = f"http://{SHELLY_IP}/emeter/"
    url_api2 = f"http://{SHELLY_IP}/rpc"
    url_api3 = f"http://{DTU_IP}/api/limit/status"

    if EM3PRO:
        # hole Echtzeitdaten vom Shelly
        request_daten = {"id":1,"method":"EM.GetStatus","params":{"id":0}}
        if request_daten:
            json_response = post_request(url_api2, json.dumps(request_daten))
            # print(json.dumps(json_response, indent=4))
            # print(f"total_act_power: {json_response['result']['total_act_power']}")
            grid_sum=json_response['result']['total_act_power']
        else:
            print("Konnte keine Daten von Shelly EM3 Pro empfangen.")
            time.sleep(STANDARD_TIMEOUT) # warte STANDARD_TIMEOUT bevor wir es erneut probieren.
            continue
    else:
        phase_a = get_request(f'{url_api1}/0',
                    {'Content-Type': 'application/json'})['power']
        if phase_a:
            phase_b = get_request(f'{url_api1}/1',
                        {'Content-Type': 'application/json'})['power']
        else:
            print("Konnte keine Daten von Shelly EM3 empfangen.")
            time.sleep(STANDARD_TIMEOUT) # warte STANDARD_TIMEOUT bevor wir es erneut probieren.
            continue
        if phase_b:
            phase_c = get_request(f'{url_api1}/2',
                        {'Content-Type': 'application/json'})['power']
            grid_sum = phase_a + phase_b + phase_c # Aktueller Bezug - rechnet alle Phasen zusammen
        else:
            print("Konnte keine Daten von Shelly EM3 empfangen.")
            time.sleep(STANDARD_TIMEOUT) # warte STANDARD_TIMEOUT bevor wir es erneut probieren.
            continue

    # Werte setzen
    print(f'\nBezug: {round(grid_sum, 1)} W, Produktion: {round(power, 1)} W,\
 Verbrauch: {round(grid_sum + power, 1)} W')
    if REACHABLE:
        limit_status = get_request(url_api3)
        if limit_status:
            limit_status_val=limit_status[SERIAL]['limit_set_status']
            print(f'limit status: {limit_status_val}')
            # Der Inverter verrät uns, ob er mit dem Setzen des letzten Wertes fertig geworden ist.
            # Wir setzen keinen neuen Wert, bevor der Inverter kein 'Ok' liefert.
            if limit_status_val != 'Ok':
                print('Letzte Einstellung noch nicht gesetzt. Warte bis zum nächsten Durchlauf...')
                time.sleep(STANDARD_TIMEOUT) # wait
                continue
        SET_POINT = grid_sum + altes_limit - 5 # Neues Limit in Watt

        # Fange oberes Limit ab
        if SET_POINT > MAXIMUM_WR:
            SET_POINT = MAXIMUM_WR
            print(f'Setpoint auf Maximum: {MAXIMUM_WR} W')
        # Fange unteres Limit ab
        elif SET_POINT < MINIMUM_WR:
            SET_POINT = MINIMUM_WR
            print(f'Setpoint auf Minimum: {MINIMUM_WR} W')
        else:
            print(f'Setpoint berechnet: {round(grid_sum, 1)} W + {round(altes_limit, 1)}\
 W - 5 W = {round(SET_POINT, 1)} W')

        if abs(SET_POINT - altes_limit) > 2:    # Weil der Wert, den wir setzen nicht 100%
                                                # der Wert ist den wir auslesen,
                                                # lassen wir ein Differenz von 2W zu.
            print(f'Setze Inverterlimit von {round(altes_limit, 1)}\
 W auf {round(SET_POINT, 1)} W... ', end='')
            # Neues Limit setzen
            r = post_request(
                f'http://{DTU_IP}/api/limit/config',
                f'data={{"serial":"{SERIAL}", "limit_type":0, "limit_value":{SET_POINT}}}',
                HTTPBasicAuth(DTU_NUTZER, DTU_PASSWORT),
                {'Content-Type': 'application/x-www-form-urlencoded'}
            )
            if r:
                print(f'Konfiguration gesendet: ({r["type"]}, {r["message"]})')
            else:
                print('Fehler: beim Senden der Konfiguration')
    REACHABLE = False
    sys.stdout.flush() # write out cached messages to stdout
    time.sleep(STANDARD_TIMEOUT) # wait
