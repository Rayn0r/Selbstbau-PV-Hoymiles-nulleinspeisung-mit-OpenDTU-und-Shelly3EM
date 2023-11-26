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
from requests.auth import HTTPBasicAuth
import requests

# Diese Daten müssen angepasst werden:
SERIAL = "112100000000" # Seriennummer des Hoymiles Wechselrichters
MAXIMUM_WR = 300 # Maximale Ausgabe des Wechselrichters
MINIMUM_WR = 100 # Minimale Ausgabe des Wechselrichters

DTU_IP = '192.100.100.20' # IP Adresse von OpenDTU
DTU_NUTZER = 'admin' # OpenDTU Nutzername
DTU_PASSWORT = 'openDTU42' # OpenDTU Passwort

SHELLY_IP = '192.100.100.30' # IP Adresse von Shelly 3EM

STANDARD_TIMEOUT = 5    # Zeit in Sekunden
EM3PRO = True           # Wird ein Shelly EM3 Pro eingesetzt?

def get_request(url_name, header_definition=''):
    """
    Retrieve simple value from URL and return it
    in case things go wrong, return False
    """
    try:
        daten = requests.get(url = url_name, timeout=STANDARD_TIMEOUT,
                    headers=header_definition ).json()
        return daten
    except Exception as fehler:
        print(f"Fehler {fehler} beim Aufruf von {url_name}")
        return False

def post_request(url_name, post_daten, auth_daten='', header_definition=''):
    """
    send post request to 
    """
    try:
        daten = requests.post(url_name, data=json.dumps(post_daten),
                    timeout=STANDARD_TIMEOUT, auth=auth_daten, headers=header_definition).json()
        return daten
    except Exception as fehler:
        print(f"Fehler {fehler} beim Aufruf von {url_name}")
        return False

while True:
    # Nimmt Daten von der openDTU Rest-API und übersetzt sie in ein json-Format
    r_dtu = get_request(f'http://{DTU_IP}/api/livedata/status/inverters')
    if r_dtu:
        # Selektiert spezifische Daten aus der json response
        reachable   = r_dtu['inverters'][0]['reachable'] # Ist DTU erreichbar?
        producing   = int(r_dtu['inverters'][0]['producing']) # Produziert der Wechselrichter etwas?
        altes_limit = int(r_dtu['inverters'][0]['limit_absolute']) # Altes Limit
        power_dc    = r_dtu['inverters'][0]['AC']['0']['Power DC']['v']  # Lieferung DC vom Panel
        power       = r_dtu['inverters'][0]['AC']['0']['Power']['v'] # Abgabe BKW AC in Watt
        # print(f"{reachable} {producing} {altes_limit} {power_dc} {power}")
    else:
        print("Konnte keine Daten von OpenDTU empfangen.")
        time.sleep(STANDARD_TIMEOUT) # warte STANDARD_TIMEOUT bevor wir es erneut probieren.
        continue

    # Nimmt Daten von der Shelly 3EM (Pro) Rest-API(2) und übersetzt sie ins json-Format
    url_api1 = f"http://{SHELLY_IP}/emeter/"
    url_api2 = f"http://{SHELLY_IP}/rpc"
    if EM3PRO:

        # hole Echtzeitdaten vom Shelly
        request_daten = {"id":1,"method":"EM.GetStatus","params":{"id":0}}
        if request_daten:
            json_response = post_request(url_api2, request_daten)
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
    if reachable:
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
            print(f'Setpoint berechnet: {round(grid_sum, 1)} W + {round(altes_limit, 1)
                } W - 5 W = {round(SET_POINT, 1)} W')

        if SET_POINT != altes_limit:
            print(f'Setze Inverterlimit von {round(altes_limit, 1)
                } W auf {round(SET_POINT, 1)} W... ', end='')
            # Neues Limit setzen
            try:
                r = post_request(
                    f'http://{DTU_IP}/api/limit/config',
                    f'data={{"serial":"{SERIAL}", "limit_type":0, "limit_value":{SET_POINT}}}',
                    HTTPBasicAuth(DTU_NUTZER, DTU_PASSWORT),
                    {'Content-Type': 'application/x-www-form-urlencoded'}
                )
                print(f'Konfiguration gesendet ({r.json()["type"]})')
            except Exception as Fehler:
                print(f'Fehler: {Fehler} beim Senden der Konfiguration')
    sys.stdout.flush() # write out cached messages to stdout
    time.sleep(5) # wait
