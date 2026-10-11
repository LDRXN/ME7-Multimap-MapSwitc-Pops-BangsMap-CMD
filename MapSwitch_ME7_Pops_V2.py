#!/usr/bin/env python3
"""Bosch ME7.5 MultiMap Builder v0.2

This version ports the PRJ-Tuning/me7-tools Java XML locator literally for
Audi ME7 map definitions required by MultiMap. There are no ECU-specific map
addresses in the detector.

Scope:
- 1 MiB Bosch ME7.5 BIN.
- Locate LAMFA, KFZW, KFZW2, LDRXN, LDRXNZK, KRKTE and referenced axes.
- Exact Java/XML locator for directly supported items.
- XML/C167 structural pair extraction for KFZW/KFZW2 and LDRXN/LDRXNZK.
- Same Audi DPP address calculation and axis-length fallback as Me7XmlPlugin.
- Bank 1 stays at original addresses; selected bank count creates N-1 copies.
- Installs runtime bank switching and map selectors for BENCH validation.
- Generates a matching TunerPro XDF for the selected MultiMap banks.
- Input BIN is never overwritten.
"""
from __future__ import annotations
import argparse
import io
import sys
import re
import csv
from datetime import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

APP_NAME = "Bosch ME7.5 MultiMap Builder"
APP_VERSION = "0.40-tach-mifab-anchor"
EXPECTED_SIZE = 1024 * 1024
REQUIRED_MAPS = ("LAMFA", "KFZW", "KFZW2", "LDRXN", "LDRXNZK", "KRKTE")
EXPERIMENTAL_COPY_MAPS = ("KFZWMN", "KFNWEGM", "KFTVSA", "KFTVSAKAT")
MIN_BANKS=2
MAX_BANKS=4
ALIGNMENT=0x10
PREFERRED_CAL_START=0xC0000
FALLBACK_CAL_START=0x70000
NS = {"m":"http://prj-tuning.com/mapdef"}

class MapSwitchError(RuntimeError): pass

_USE_COLOR = bool(getattr(sys.stdout, "isatty", lambda: False)())
_RESET = "\033[0m" if _USE_COLOR else ""
_BOLD = "\033[1m" if _USE_COLOR else ""
_DIM = "\033[2m" if _USE_COLOR else ""
_GREEN = "\033[92m" if _USE_COLOR else ""
_YELLOW = "\033[93m" if _USE_COLOR else ""
_BLUE = "\033[94m" if _USE_COLOR else ""
_RED = "\033[91m" if _USE_COLOR else ""
_CYAN = "\033[96m" if _USE_COLOR else ""

def _tag(text:str, color:str) -> str:
    return f"{color}{_BOLD}{text}{_RESET}" if _USE_COLOR else text

def info(s): print(f"{_tag('[INFO]', _BLUE)} {s}")
def ok(s): print(f"{_tag('[ OK ]', _GREEN)} {s}")
def warn(s): print(f"{_tag('[WARN]', _YELLOW)} {s}")

def section(title:str):
    width=72
    print()
    print(_tag("─"*width, _CYAN))
    print(_tag(f" {title}", _CYAN))
    print(_tag("─"*width, _CYAN))

def banner(title:str, subtitle:str=""):
    width=72
    inner=width-2
    print(_tag("╔"+"═"*inner+"╗", _CYAN))
    print(_tag("║"+title.center(inner)+"║", _CYAN))
    if subtitle:
        print(_tag("╠"+"═"*inner+"╣", _CYAN))
        line=subtitle.center(inner)
        print((_DIM+"║"+line+"║"+_RESET) if _USE_COLOR else "║"+line+"║")
    print(_tag("╚"+"═"*inner+"╝", _CYAN))

def menu_box(title:str, lines:list[str]):
    width=72
    inner=width-2
    print()
    print(_tag("┌"+"─"*inner+"┐", _CYAN))
    title_line=(" "+title+" ").center(inner,"─")
    print(_tag("│"+title_line+"│", _CYAN))
    for line in lines:
        txt=(" "+line)[:inner]
        print("│"+txt.ljust(inner)+"│")
    print(_tag("└"+"─"*inner+"┘", _CYAN))

def prompt_choice(label:str, default:str="") -> str:
    d=f" [{default}]" if default else ""
    return input(f"{_tag('›',_CYAN)} {label}{d}: ").strip()

class CsvAuditLog:
    """
    Clean human-readable CSV report.
    The first column contains a readable report when the CSV is opened as text,
    while the remaining columns keep bank/map data easy to filter in Excel/Calc.
    """
    COLUMNS = ["REPORT","BANK","NAME","SOURCE","DESTINATION","SIZE_BYTES","ADDRESS","STATUS","DETAILS"]

    def __init__(self, path:Path):
        self.path=path
        self.rows=[]

    def title(self, text:str):
        self.rows.append({"__title__": text})

    def blank(self):
        self.rows.append({"__blank__": True})

    def section(self, title:str):
        self.rows.append({"__section__": title})

    def line(self, text:str="", **fields):
        row={k:"" for k in self.COLUMNS}
        row["REPORT"]=text
        mapping={
            "bank":"BANK","name":"NAME","source":"SOURCE","destination":"DESTINATION",
            "size_bytes":"SIZE_BYTES","address":"ADDRESS","status":"STATUS","details":"DETAILS"
        }
        for k,v in fields.items():
            if k in mapping and v is not None:
                row[mapping[k]]=str(v)
        self.rows.append(row)

    # Backward-compatible helper used by older audit code paths.
    def add(self, category:str, item:str, status:str="", **fields):
        details=str(fields.pop("details","") or "")
        name=str(fields.get("name","") or "")
        address=str(fields.get("address","") or "")
        source=str(fields.get("source","") or "")
        destination=str(fields.get("destination","") or "")
        size=str(fields.get("size_bytes","") or "")
        bank=str(fields.get("bank","") or "")
        parts=[item]
        if name: parts.append(name)
        if address: parts.append(address)
        if source and destination and source!=destination: parts.append(f"{source} -> {destination}")
        elif source and not address: parts.append(source)
        if size: parts.append(f"{size} B")
        if details: parts.append(details)
        self.line("  ".join(parts),bank=bank,name=name,source=source,
                  destination=destination,size_bytes=size,address=address,
                  status=status,details=details)

    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="", encoding="utf-8-sig") as f:
            w=csv.writer(f)
            w.writerow(["═"*72])
            w.writerow([" Bosch ME7.5 MultiMap Builder"])
            w.writerow([" ME7.5 1MB • MultiMap CSV LOG"])
            w.writerow(["═"*72])
            w.writerow([])
            for row in self.rows:
                if "__blank__" in row:
                    w.writerow([])
                elif "__title__" in row:
                    w.writerow([row["__title__"]])
                elif "__section__" in row:
                    w.writerow([])
                    w.writerow(["─"*72])
                    w.writerow([f" {row['__section__']}"])
                    w.writerow(["─"*72])
                else:
                    w.writerow([row.get(c,"") for c in self.COLUMNS])


# XDF metadata taken from the supplied DR definition CSV.
# Addresses are NOT taken from this table; the builder still uses addresses
# detected from the current BIN and addresses allocated for copied banks.
DR_XDF_META = {
    "KFZWMN": {
        "description": "My-Ignition",
        "z": {"unit":"grad KW","factor":0.75,"offset":0.0,"precision":2,"bits":8,"signed":True,"dataorg":"eByte"},
        "x": {"unit":"%","factor":0.75,"offset":0.0,"precision":3,"bits":8,"signed":False,"dataorg":"eByte"},
        "y": {"unit":"Upm","factor":40.0,"offset":0.0,"precision":0,"bits":8,"signed":False,"dataorg":"eByte"},
    },
    "KFNWEGM": {
        "description": "Re-inserting speed characteristic field",
        "z": {"unit":"Upm","factor":40.0,"offset":0.0,"precision":0,"bits":8,"signed":False,"dataorg":"eByte"},
        "x": {"unit":"-","factor":1.0,"offset":0.0,"precision":2,"bits":8,"signed":False,"dataorg":"eByte"},
        "y": {"unit":"Grad C","factor":0.75,"offset":-48.0,"precision":2,"bits":8,"signed":False,"dataorg":"eByte"},
    },
    "KFTVSA": {
        "description": "Delay time for thrust turn-off",
        "z": {"unit":"s","factor":0.01,"offset":0.0,"precision":2,"bits":8,"signed":False,"dataorg":"eByte"},
        "x": {"unit":"Upm","factor":40.0,"offset":0.0,"precision":0,"bits":8,"signed":False,"dataorg":"eByte"},
        "y": {"unit":"Grad C","factor":0.75,"offset":-48.0,"precision":2,"bits":8,"signed":False,"dataorg":"eByte"},
    },
    "KFTVSAKAT": {
        "description": "Delay time thrust turn-off at high speeds and high catalyst temperature",
        "z": {"unit":"ms","factor":40.0,"offset":0.0,"precision":0,"bits":8,"signed":False,"dataorg":"eByte"},
        "x": {"unit":"Grad C","factor":5.0,"offset":-50.0,"precision":2,"bits":8,"signed":False,"dataorg":"eByte"},
        "y": {"unit":"Upm","factor":40.0,"offset":0.0,"precision":0,"bits":8,"signed":False,"dataorg":"eByte"},
    },
    "KFZW": {
        "description": "Timing map",
        "z": {"unit":"grad KW","factor":0.75,"offset":0.0,"precision":3,"bits":8,"signed":True,"dataorg":"eByte"},
        "x": {"unit":"%","factor":0.0234375,"offset":0.0,"precision":4,"bits":16,"signed":False,"dataorg":"eLoHi"},
        "y": {"unit":"U/min","factor":0.25,"offset":0.0,"precision":3,"bits":16,"signed":False,"dataorg":"eLoHi"},
    },
    "KFZW2": {
        "description": "Timing map Variant 2",
        "z": {"unit":"grad KW","factor":0.75,"offset":0.0,"precision":3,"bits":8,"signed":True,"dataorg":"eByte"},
        "x": {"unit":"%","factor":0.0234375,"offset":0.0,"precision":4,"bits":16,"signed":False,"dataorg":"eLoHi"},
        "y": {"unit":"U/min","factor":0.25,"offset":0.0,"precision":3,"bits":16,"signed":False,"dataorg":"eLoHi"},
    },
    "KRKTE": {
        "description": "Conversion relative Fuel mass rk in effective Injection time te",
        "z": {"unit":"ms/%","factor":0.000111111111,"offset":0.0,"precision":5,"bits":16,"signed":False,"dataorg":"eLoHi"},
    },
    "LAMFA": {
        "description": "Lambda Driver's wish",
        "z": {"unit":"-","factor":0.0078125,"offset":0.0,"precision":4,"bits":8,"signed":False,"dataorg":"eByte"},
        "x": {"unit":"%","factor":0.003051757813,"offset":0.0,"precision":4,"bits":16,"signed":False,"dataorg":"eLoHi"},
        "y": {"unit":"Upm","factor":40.0,"offset":0.0,"precision":1,"bits":8,"signed":False,"dataorg":"eByte"},
    },
    "LDRXN": {
        "description": "maximum filling LDR",
        "z": {"unit":"%","factor":0.0234375,"offset":0.0,"precision":4,"bits":16,"signed":False,"dataorg":"eLoHi"},
        "x": {"unit":"U/min","factor":0.25,"offset":0.0,"precision":-1,"bits":16,"signed":False,"dataorg":"eLoHi"},
    },
    "LDRXNZK": {
        "description": "maximum filling LDR At Duration knocking",
        "z": {"unit":"%","factor":0.0234375,"offset":0.0,"precision":4,"bits":16,"signed":False,"dataorg":"eLoHi"},
        "x": {"unit":"U/min","factor":0.25,"offset":0.0,"precision":-1,"bits":16,"signed":False,"dataorg":"eLoHi"},
    },
}

def _xdf_type_flags(meta:dict) -> str:
    flags=0
    if meta.get("signed"):
        flags |= 0x01
    # In TunerPro XDF mmedtypeflags bit 1 is used here for LoHi / little-endian.
    if meta.get("bits",8) > 8 and meta.get("dataorg") == "eLoHi":
        flags |= 0x02
    return f"0x{flags:02X}"

def _xdf_set_text(parent, tag:str, value):
    el=parent.find(tag)
    if el is None:
        el=ET.SubElement(parent,tag)
    el.text=str(value)
    return el

def _xdf_apply_axis_meta(axis_el, meta:dict):
    data=axis_el.find("EMBEDDEDDATA")
    if data is None:
        data=ET.SubElement(axis_el,"EMBEDDEDDATA")
    data.set("mmedelementsizebits",str(meta["bits"]))
    data.set("mmedtypeflags",_xdf_type_flags(meta))
    data.set("mmedmajorstridebits",str(meta["bits"]))
    data.set("mmedminorstridebits","0")
    _xdf_set_text(axis_el,"units",meta["unit"])
    precision=meta.get("precision",0)
    if precision is not None and precision >= 0:
        _xdf_set_text(axis_el,"decimalpl",precision)
    else:
        old=axis_el.find("decimalpl")
        if old is not None:
            axis_el.remove(old)
    _xdf_set_math(axis_el,meta["factor"],meta.get("offset",0.0))

def _xdf_apply_z_meta(axis_el, meta:dict):
    data=axis_el.find("EMBEDDEDDATA")
    if data is None:
        data=ET.SubElement(axis_el,"EMBEDDEDDATA")
    data.set("mmedelementsizebits",str(meta["bits"]))
    data.set("mmedtypeflags",_xdf_type_flags(meta))
    _xdf_set_text(axis_el,"units",meta["unit"])
    precision=meta.get("precision",0)
    if precision is not None and precision >= 0:
        _xdf_set_text(axis_el,"decimalpl",precision)
    _xdf_set_math(axis_el,meta["factor"],meta.get("offset",0.0))


# XDF template supplied for the MultiMap editor layout.
EMBEDDED_XDF_TEMPLATE = '<!-- Written 08/09/2026 21:53:00 -->\n<XDFFORMAT version="1.80">\n  <XDFHEADER>\n    <flags>0x1</flags>\n    <fileversion>Version 1</fileversion>\n    <deftitle>data/8D0907551M-20110122</deftitle>\n    <description>Original</description>\n    <author>mesim translator</author>\n    <BASEOFFSET offset="0" subtract="0" />\n    <DEFAULTS datasizeinbits="8" sigdigits="2" outputtype="1" signed="0" lsbfirst="0" float="0" />\n    <REGION type="0xFFFFFFFF" startaddress="0x0" size="0x100000" regioncolor="0x0" regionflags="0x0" name="Binary File" desc="This region describes the bin file edited by this XDF" />\n    <CATEGORY index="0x0" name="My maps" />\n    <CATEGORY index="0x1" name="Hexdump" />\n    <CATEGORY index="0x2" name="Boost PID" />\n    <CATEGORY index="0x3" name="Boost" />\n    <CATEGORY index="0x4" name="Throttle" />\n    <CATEGORY index="0x5" name="Boost ldob" />\n    <CATEGORY index="0x6" name="MAF" />\n    <CATEGORY index="0x7" name="Timing" />\n    <CATEGORY index="0x8" name="Variable cam timing (NWS)" />\n    <CATEGORY index="0x9" name="Fueling" />\n    <CATEGORY index="0xA" name="Boost DIAG" />\n    <CATEGORY index="0xB" name="Boost pssol and plsol" />\n    <CATEGORY index="0xC" name="Engine speed maximum" />\n    <CATEGORY index="0xD" name="Boost rlmax" />\n    <CATEGORY index="0xE" name="Fuel consumption indication" />\n    <CATEGORY index="0xF" name="Engine speed (idle)" />\n    <CATEGORY index="0x10" name="Torque" />\n    <CATEGORY index="0x11" name="Start and warm up" />\n    <CATEGORY index="0x12" name="Knock regulation" />\n    <CATEGORY index="0x13" name="Speed limiter" />\n    <CATEGORY index="0x14" name="Misfire recognition" />\n    <CATEGORY index="0x15" name="Emissions" />\n    <CATEGORY index="0x16" name="Brake booster" />\n    <CATEGORY index="0x17" name="Unknown" />\n    <CATEGORY index="0x18" name="Engine speed maximum (elevated)" />\n    <CATEGORY index="0x19" name="Project configuration" />\n    <CATEGORY index="0x1A" name="Exhaust gas temperature" />\n    <CATEGORY index="0x1B" name="Engine speed (misc)" />\n    <CATEGORY index="0x1C" name="KOS: 128.10    AC Compressor  - Control" />\n    <CATEGORY index="0x1D" name="KDIAG" />\n    <CATEGORY index="0x1E" name="Emissions DTCs" />\n    <CATEGORY index="0x1F" name="Exhaust gas temperature DTCs" />\n    <CATEGORY index="0x20" name="DKAT" />\n    <CATEGORY index="0x21" name="LAMFA" />\n    <CATEGORY index="0x22" name="ZUESZ" />\n    <CATEGORY index="0x23" name="ARMD" />\n    <CATEGORY index="0x24" name="MDBGRG" />\n    <CATEGORY index="0x25" name="SLS" />\n    <CATEGORY index="0x26" name="MDMAX" />\n    <CATEGORY index="0x27" name="GGDSAS" />\n    <CATEGORY index="0x28" name="LAMKO" />\n    <CATEGORY index="0x29" name="LREB" />\n    <CATEGORY index="0x2A" name="MDFAW" />\n    <CATEGORY index="0x2B" name="MDVER" />\n    <CATEGORY index="0x2C" name="ATR" />\n    <CATEGORY index="0x2D" name="DATR" />\n    <CATEGORY index="0x2E" name="DATS" />\n    <CATEGORY index="0x2F" name="GGATS" />\n    <CATEGORY index="0x30" name="Hack" />\n    <CATEGORY index="0x31" name="PROKON" />\n    <CATEGORY index="0x32" name="LR" />\n    <CATEGORY index="0x33" name="GGLSV" />\n    <CATEGORY index="0x34" name="Multi-map" />\n    <CATEGORY index="0x35" name="ZWGRU" />\n    <CATEGORY index="0x36" name="LDRLMX" />\n    <CATEGORY index="0x37" name="DEKON" />\n    <CATEGORY index="0x38" name="LAMBTS" />\n    <CATEGORY index="0x39" name="LAMFAW" />\n    <CATEGORY index="0x3A" name="RKTI" />\n    <CATEGORY index="0x3B" name="KVA" />\n  </XDFHEADER>\n  <XDFCONSTANT uniqueid="0x2520">\n    <title>KRKTE</title>\n    <description>Conversion of relative fuel mass (rk) to effective injector on time (te)</description>\n    <CATEGORYMEM index="0" category="10" />\n    <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1C2BC" mmedelementsizebits="16" mmedmajorstridebits="0" mmedminorstridebits="0" />\n    <units>ms/%</units>\n    <decimalpl>5</decimalpl>\n    <datatype>0</datatype>\n    <unittype>0</unittype>\n    <DALINK index="0" />\n    <MATH equation="0.000167 * X">\n      <VAR id="X" />\n    </MATH>\n  </XDFCONSTANT>\n  <XDFCONSTANT uniqueid="0x7F2B">\n    <title>map_2_KRKTE</title>\n    <description>Multi-map set 2:&#013;&#010;Conversion of relative fuel mass (rk) to effective injector on time (te)</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="10" />\n    <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1712A" mmedelementsizebits="16" mmedmajorstridebits="0" mmedminorstridebits="0" />\n    <decimalpl>5</decimalpl>\n    <datatype>51</datatype>\n    <unittype>85</unittype>\n    <DALINK index="0" />\n    <MATH equation="0.000167 * X">\n      <VAR id="X" />\n    </MATH>\n  </XDFCONSTANT>\n  <XDFCONSTANT uniqueid="0x22B">\n    <title>map_3_KRKTE</title>\n    <description>Multi-map set 3:&#013;&#010;Conversion of relative fuel mass (rk) to effective injector on time (te)</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="10" />\n    <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1712C" mmedelementsizebits="16" mmedmajorstridebits="0" mmedminorstridebits="0" />\n    <decimalpl>5</decimalpl>\n    <datatype>51</datatype>\n    <unittype>85</unittype>\n    <DALINK index="0" />\n    <MATH equation="0.000167 * X">\n      <VAR id="X" />\n    </MATH>\n  </XDFCONSTANT>\n  <XDFCONSTANT uniqueid="0x7D3">\n    <title>map_4_KRKTE</title>\n    <description>Multi-map set 4:&#013;&#010;Conversion of relative fuel mass (rk) to effective injector on time (te)</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="10" />\n    <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1712E" mmedelementsizebits="16" mmedmajorstridebits="0" mmedminorstridebits="0" />\n    <decimalpl>5</decimalpl>\n    <datatype>51</datatype>\n    <unittype>85</unittype>\n    <DALINK index="0" />\n    <MATH equation="0.000167 * X">\n      <VAR id="X" />\n    </MATH>\n  </XDFCONSTANT>\n  <XDFTABLE uniqueid="0x2521" flags="0x0">\n    <title>KFZW</title>\n    <description>Ignition angle map</description>\n    <CATEGORYMEM index="0" category="8" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100FF" mmedelementsizebits="8" mmedcolcount="12" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>%</units>\n      <indexcount>12</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100C2" mmedelementsizebits="8" mmedcolcount="16" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*40">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x01" mmedaddress="0x11C72" mmedelementsizebits="8" mmedrowcount="16" mmedcolcount="12" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>grad KW</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.750000 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0xA3B" flags="0x0">\n    <title>map_2_KFZW</title>\n    <description>Multi-map set 2:&#013;&#010;Ignition angle map</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="54" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x100FF" mmedelementsizebits="8" mmedcolcount="12" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>%</units>\n      <indexcount>12</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100C2" mmedelementsizebits="8" mmedcolcount="16" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*40">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x01" mmedaddress="0x17130" mmedelementsizebits="8" mmedrowcount="16" mmedcolcount="12" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>grad KW</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x1506" flags="0x0">\n    <title>map_3_KFZW</title>\n    <description>Multi-map set 3:&#013;&#010;Ignition angle map</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="54" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100FF" mmedelementsizebits="8" mmedcolcount="12" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>%</units>\n      <indexcount>12</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100C2" mmedelementsizebits="8" mmedcolcount="16" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*40">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x01" mmedaddress="0x171F0" mmedelementsizebits="8" mmedrowcount="16" mmedcolcount="12" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>grad KW</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="X*.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x3464" flags="0x0">\n    <title>map_4_KFZW</title>\n    <description>Multi-map set 4:&#013;&#010;Ignition angle map</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="54" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100FF" mmedelementsizebits="8" mmedcolcount="12" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>%</units>\n      <indexcount>12</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100C2" mmedelementsizebits="8" mmedcolcount="16" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*40">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x01" mmedaddress="0x172B0" mmedelementsizebits="8" mmedrowcount="16" mmedcolcount="12" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x2522" flags="0x0">\n    <title>KFZW2</title>\n    <description>Ignition angle map (variant 2)</description>\n    <CATEGORYMEM index="0" category="8" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100FF" mmedelementsizebits="8" mmedcolcount="12" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>%</units>\n      <indexcount>12</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100C2" mmedelementsizebits="8" mmedcolcount="16" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*40">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x01" mmedaddress="0x11D32" mmedelementsizebits="8" mmedrowcount="16" mmedcolcount="12" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>grad KW</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.750000 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x42BB" flags="0x0">\n    <title>map_2_KFZW2</title>\n    <description>Multi-map set 2:&#013;&#010;Ignition angle map (variant 2)</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="54" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100FF" mmedelementsizebits="8" mmedcolcount="12" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>%</units>\n      <indexcount>12</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100C2" mmedelementsizebits="8" mmedcolcount="16" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*40">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x01" mmedaddress="0x17370" mmedelementsizebits="8" mmedrowcount="16" mmedcolcount="12" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>grad KW</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x84F" flags="0x0">\n    <title>map_3_KFZW2</title>\n    <description>Multi-map set 3:&#013;&#010;Ignition angle map (variant 2)</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="54" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100FF" mmedelementsizebits="8" mmedcolcount="12" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>%</units>\n      <indexcount>12</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100C2" mmedelementsizebits="8" mmedcolcount="16" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*40">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x01" mmedaddress="0x17430" mmedelementsizebits="8" mmedrowcount="16" mmedcolcount="12" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>grad KW</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x31B8" flags="0x0">\n    <title>map_4_KFZW2</title>\n    <description>Multi-map set 4:&#013;&#010;Ignition angle map (variant 2)</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="54" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100FF" mmedelementsizebits="8" mmedcolcount="12" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>%</units>\n      <indexcount>12</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x100C2" mmedelementsizebits="8" mmedcolcount="16" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*40">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x01" mmedaddress="0x174F0" mmedelementsizebits="8" mmedrowcount="16" mmedcolcount="12" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>grad KW</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="X*0.75">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x252B" flags="0x0">\n    <title>LAMFA</title>\n    <description>Lambda - driver desired</description>\n    <CATEGORYMEM index="0" category="58" />\n    <CATEGORYMEM index="1" category="10" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1C382" mmedelementsizebits="16" mmedcolcount="6" mmedmajorstridebits="16" mmedminorstridebits="0" />\n      <units>mrfa_w</units>\n      <indexcount>6</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="0.003052 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x1C372" mmedelementsizebits="8" mmedcolcount="15" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>15</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="40 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedaddress="0x1C38E" mmedelementsizebits="8" mmedrowcount="15" mmedcolcount="6" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>-</units>\n      <decimalpl>3</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.007813 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x252C" flags="0x0">\n    <title>map_2_LAMFA</title>\n    <description>Multi-map set 2:&#013;&#010;Lambda - driver desired</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="58" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1C382" mmedelementsizebits="16" mmedcolcount="6" mmedmajorstridebits="16" mmedminorstridebits="0" />\n      <units>mrfa_w</units>\n      <indexcount>6</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="0.003052 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x1C372" mmedelementsizebits="8" mmedcolcount="15" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>15</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="40 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedaddress="0x17922" mmedelementsizebits="8" mmedrowcount="15" mmedcolcount="6" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>-</units>\n      <decimalpl>3</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.007813 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x252D" flags="0x0">\n    <title>map_3_LAMFA</title>\n    <description>Multi-map set 3:&#013;&#010;Lambda - driver desired</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="58" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1C382" mmedelementsizebits="16" mmedcolcount="6" mmedmajorstridebits="16" mmedminorstridebits="0" />\n      <units>mrfa_w</units>\n      <indexcount>6</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="0.003052 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x1C372" mmedelementsizebits="8" mmedcolcount="15" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>15</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="40 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedaddress="0x1797C" mmedelementsizebits="8" mmedrowcount="15" mmedcolcount="6" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>-</units>\n      <decimalpl>3</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.007813 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x252E" flags="0x0">\n    <title>map_4_LAMFA</title>\n    <description>Multi-map set 4:&#013;&#010;Lambda - driver desired</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="58" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1C382" mmedelementsizebits="16" mmedcolcount="6" mmedmajorstridebits="16" mmedminorstridebits="0" />\n      <units>mrfa_w</units>\n      <indexcount>6</indexcount>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="0.003052 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedaddress="0x1C372" mmedelementsizebits="8" mmedcolcount="15" mmedmajorstridebits="8" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>15</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="40 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedaddress="0x179D6" mmedelementsizebits="8" mmedrowcount="15" mmedcolcount="6" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>-</units>\n      <decimalpl>3</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.007813 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x252F" flags="0x0">\n    <title>LDRXN</title>\n    <description>Maximum specified load</description>\n    <CATEGORYMEM index="0" category="14" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1DCD4" mmedelementsizebits="16" mmedcolcount="16" mmedmajorstridebits="16" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <decimalpl>0</decimalpl>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <MATH equation="0.250000 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedelementsizebits="16" mmedmajorstridebits="-32" mmedminorstridebits="0" />\n      <units>-</units>\n      <indexcount>1</indexcount>\n      <outputtype>4</outputtype>\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <LABEL index="0" value="-" />\n      <MATH equation="X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x1DCF4" mmedelementsizebits="16" mmedrowcount="1" mmedcolcount="16" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>%</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.023438 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x4C20" flags="0x0">\n    <title>map_2_LDRXN</title>\n    <description>Multi-map set 2:&#013;&#010;Maximum specified load</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="55" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x175B2" mmedelementsizebits="16" mmedcolcount="16" mmedmajorstridebits="16" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.25">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedelementsizebits="8" mmedmajorstridebits="-32" mmedminorstridebits="0" />\n      <units>-</units>\n      <indexcount>1</indexcount>\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <LABEL index="0" value="0.00" />\n      <MATH equation="X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x175D2" mmedelementsizebits="16" mmedrowcount="1" mmedcolcount="16" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>%</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.023438 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x5310" flags="0x0">\n    <title>map_3_LDRXN</title>\n    <description>Multi-map set 3:&#013;&#010;Maximum specified load</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="55" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x175F4" mmedelementsizebits="16" mmedcolcount="16" mmedmajorstridebits="16" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.25">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedelementsizebits="8" mmedmajorstridebits="-32" mmedminorstridebits="0" />\n      <units>-</units>\n      <indexcount>1</indexcount>\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <LABEL index="0" value="0.00" />\n      <MATH equation="X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x17614" mmedelementsizebits="16" mmedrowcount="1" mmedcolcount="16" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>%</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.023438 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n  <XDFTABLE uniqueid="0x3DA1" flags="0x0">\n    <title>map_4_LDRXN</title>\n    <description>Multi-map set 4:&#013;&#010;Maximum specified load</description>\n    <CATEGORYMEM index="0" category="53" />\n    <CATEGORYMEM index="1" category="55" />\n    <XDFAXIS id="x" uniqueid="0x0">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x17636" mmedelementsizebits="16" mmedcolcount="16" mmedmajorstridebits="16" mmedminorstridebits="0" />\n      <units>RPM</units>\n      <indexcount>16</indexcount>\n      <outputtype>2</outputtype>\n      <embedinfo type="1" />\n      <datatype>6</datatype>\n      <unittype>4</unittype>\n      <DALINK index="0" />\n      <MATH equation="X*0.25">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="y" uniqueid="0x0">\n      <EMBEDDEDDATA mmedelementsizebits="8" mmedmajorstridebits="-32" mmedminorstridebits="0" />\n      <units>-</units>\n      <indexcount>1</indexcount>\n      <datatype>0</datatype>\n      <unittype>0</unittype>\n      <DALINK index="0" />\n      <LABEL index="0" value="0.00" />\n      <MATH equation="X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n    <XDFAXIS id="z">\n      <EMBEDDEDDATA mmedtypeflags="0x02" mmedaddress="0x17656" mmedelementsizebits="16" mmedrowcount="1" mmedcolcount="16" mmedmajorstridebits="0" mmedminorstridebits="0" />\n      <units>%</units>\n      <decimalpl>2</decimalpl>\n      <min>0.000000</min>\n      <max>255.000000</max>\n      <outputtype>1</outputtype>\n      <MATH equation="0.023438 * X">\n        <VAR id="X" />\n      </MATH>\n    </XDFAXIS>\n  </XDFTABLE>\n</XDFFORMAT>\n'

EMBEDDED_XML = {
    'CATR.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>CATR</id>\n\t<pattern>D7 40 XX XX F3 F8 MMXX XX 69 81 2D 02 6F 88 0D 01</pattern>\n\t<address>\n\t\t<dppOffset>-4</dppOffset>\n\t</address>\n</map>\n',
    'CDKAT.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>CDKAT</id>\n\t<pattern>D7 40 XX XX C2 F4 MMXX XX 68 41 2D 05 E6 F4 00 01 74 F4</pattern>\n\t<address>\n\t\t<dppOffset>-4</dppOffset>\n\t</address>\n</map>\n',
    'CDSLS.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>CDSLS</id>\n\t<pattern>D7 40 XX XX C2 F4 MMXX XX 68 41 2D 05 E6 F4 00 04 74 F4</pattern>\n\t<address>\n\t\t<dppOffset>-4</dppOffset>\n\t</address>\n</map>\n',
    'CLRHK.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>CLRHK</id>\n\t<pattern>08 04 F7 F8 XX XX C2 F4 MMXX XX 66 F4 08 00</pattern>\n</map>\n',
    'CWDLSAHK.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>CWDLSAHK</id>\n\t<pattern>E6 FC XX XX DA XX XX XX 08 02 D7 40 XX XX XX XX MMXX XX XX XX 3D 04</pattern>\n\t<address>\n\t\t<dppOffset>-4</dppOffset>\n\t</address>\n</map>\n',
    'KFLBTS.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>KFLBTS</id>\n\t<pattern>88 XX E6 FC MMXX XX E6 FD XX XX E6 FE XX XX E6 FF XX XX DA XX XX XX 08 04 XX2 5C 54 D7</pattern>\n\t<address>\n\t\t<dppOffset>4</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.007813</factor>\n\t</conversion>\n\t<rowAxis>\n\t\t<id>SRL12GKUW</id>\n\t\t<id>SRL12GKUB</id>\n\t</rowAxis>\n\t<colAxis>\n\t\t<id>SNM16GKUB</id>\n\t</colAxis>\n</map>',
    'KFMIOP.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>KFMIOP</id>\n\t<pattern>F0 D7 DA XX XX XX F0 F4 E6 FC MMXX XX E6 FD XX XX F2 FE XX XX DA</pattern>\n\t<conversion>\n\t\t<factor>0.001526</factor>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n\t<rowAxis>\n\t\t<id>SNM16OPUW</id>\n\t</rowAxis>\n\t<colAxis>\n\t\t<id>SRL11OPUW</id>\n\t</colAxis>\n</map>\n',
    'KFZW.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>KFZW</id>\n\t<pattern>E6 FC XX XX E6 FD XX XX F2 FE XX XX F2 FF XX XX DA 00 B8 78 XX18 E6 FC MMXX XX E6 FD XX XX F2 FE XX XX F2 FF XX XX DA 00 B8 78 XX70 46 F9 7F 00 XX20 46 F9 80 FF</pattern>\n\t<conversion>\n\t\t<factor>0.75</factor>\n\t\t<signed>true</signed>\n\t</conversion>\n\t<rowAxis>\n\t\t<id>SNM16ZUUB</id>\n\t</rowAxis>\n\t<colAxis>\n\t\t<id>SRL12ZUUB</id>\n\t</colAxis>\n</map>\n',
    'KFZW2.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>KFZW2</id>\n\t<pattern>0D 2F E6 FC MMXX XX E6 FD XX XX F2 FE XX XX F2 FF XX XX DA 00 B8 78 XX18 E6 FC MMXX XX E6 FD XX XX F2 FE XX XX F2 FF XX XX DA 00 B8 78 XX70 46 F9 7F 00 XX20 46 F9 80 FF</pattern>\n\t<conversion>\n\t\t<factor>0.75</factor>\n\t\t<signed>true</signed>\n\t</conversion>\n\t<rowAxis>\n\t\t<id>SNM16ZUUB</id>\n\t</rowAxis>\n\t<colAxis>\n\t\t<id>SRL12ZUUB</id>\n\t</colAxis>\n</map>\n',
    'KRKTE.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>KRKTE</id>\n\t<pattern>F2 F4 XX XX 7C 44 E0 05 70 55</pattern>\n\t<address>\n\t\t<offset>2</offset>\n\t\t<dppOffset>-2</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.000167</factor>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n</map>\n',
    'KRKTE_8.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>KRKTE</id>\n\t<pattern>8A XX XX XX D7 40 XX XX C2 XX MMXX XX E0 05 70 55</pattern>\n\t<address>\n\t\t<dppOffset>-4</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.002133</factor>\n\t</conversion>\n</map>\n',
    'LAMFA.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>LAMFA</id>\n\t<pattern>F2 F4 XX XX 88 40 C2 F5 XX XX 88 50 E6 F4 MMXX XX E6 F5</pattern>\n\t<address>\n\t\t<dppOffset>4</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.007813</factor>\n\t\t<offset>0</offset>\n\t</conversion>\n\t<rowAxis>\n\t\t<id>LAMFA_ROWAXIS</id>\n\t</rowAxis>\n\t<colAxis>\n\t\t<id>LAMFA_COLAXIS</id>\n\t</colAxis>\n</map>\n',
    'LAMFA_COLAXIS.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>LAMFA_ROWAXIS</id>\n\t<pattern>F2 F4 XX XX 88 40 C2 F5 XX XX 88 50 E6 F4 MMXX XX E6 F5</pattern>\n\t<address>\n\t\t<offset>32</offset>\n\t\t<dppOffset>36</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>40</factor>\n\t</conversion>\n\t<length>\n\t\t<address>\n\t\t\t<offset>28</offset>\n\t\t\t<dppOffset>24</dppOffset>\n\t\t</address>\n\t</length>\n</map>\n',
    'LAMFA_ROWAXIS.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>LAMFA_COLAXIS</id>\n\t<pattern>F2 F4 XX XX 88 40 C2 F5 XX XX 88 50 E6 F4 MMXX XX E6 F5</pattern>\n\t<address>\n\t\t<offset>12</offset>\n\t\t<dppOffset>16</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.003052</factor>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n\t<length>\n\t\t<address>\n\t\t\t<offset>44</offset>\n\t\t\t<dppOffset>40</dppOffset>\n\t\t</address>\n\t</length>\n</map>\n',
    'LDRXN.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>LDRXN</id>\n\t<afterRowAxis>true</afterRowAxis>\n\t<conversion>\n\t\t<factor>0.023438</factor>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n\t<rowAxis>\n\t\t<id>LDRXN_ROWAXIS</id>\n\t</rowAxis>\n</map>',
    'LDRXNZK.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>LDRXNZK</id>\n\t<afterRowAxis>true</afterRowAxis>\n\t<conversion>\n\t\t<factor>0.023438</factor>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n\t<rowAxis>\n\t\t<id>LDRXNZK_ROWAXIS</id>\n\t</rowAxis>\n</map>',
    'LDRXNZK_ROWAXIS.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>LDRXNZK_ROWAXIS</id>\n\t<pattern>9A XX XX XX C2 F4 XX XX 5C 84 88 40 E6 FC MMXX XX E6 FD XX XX</pattern>\n\t<address>\n\t\t<dppOffset>4</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.25</factor>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n</map>',
    'LDRXN_ROWAXIS.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>LDRXN_ROWAXIS</id>\n\t<pattern>0D XX C2 F4 XX XX 5C 84 88 40 E6 FC MMXX XX E6 FD XX XX XX50 E6 F9 FF FF</pattern>\n\t<address>\n\t\t<dppOffset>4</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.25</factor>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n</map>',
    'MLHFM.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>MLHFM</id>\n\t<pattern>5C 14 D4 54 MMXX50 E6 XX FF FF F0</pattern>\n\t<conversion>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t\t<factor>0.1</factor>\n\t</conversion>\n\t<length>\n\t\t<hardcoded>512</hardcoded>\n\t</length>\n</map>',
    'MLOFS.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>MLOFS</id>\n\t<pattern>E0 XX F2 XX MMXX XX E0 XX 20 XX 30 XX F2</pattern>\n\t<pattern>5C XX 70 XX F6 XX XX XX F2 XX XX XX 22 XX MMXX XX 8D 04</pattern>\n\t<conversion>\n\t\t<factor>0.1</factor>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n</map>\n',
    'SNM16GKUB.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>SNM16GKUB</id>\n\t<pattern>88 XX E6 FC MMXX XX E6 FD XX XX E6 FE XX XX E6 FF XX XX DA XX XX XX 08 04 XX2 5C 54 D7</pattern>\n\t<address>\n\t\t<offset>8</offset>\n\t\t<dppOffset>12</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>40</factor>\n\t</conversion>\n</map>',
    'SNM16OPUW.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>SNM16OPUW</id>\n\t<pattern>DB 00 E6 FC XX XX C2 FD XX XX F2 FE XX XX DA 00 XX XX F6 F4 XX XX E6 FC MMXX XX F2 FD XX XX F2 FE XX XX DA 00 XX XX </pattern>\n\t<pattern>F6 F4 XX XX E6 FC MMXX XX F2 FD XX XX F2 FE XX XX DA 00 XX XX F6 F4 XX XX E6 FC XX XX C2 FD XX XX DA 00</pattern>\n\t<conversion>\n\t\t<factor>0.250000</factor>\n\t\t<endianness>LoHi</endianness>\n\t\t<width>2</width>\n\t\t<alt>\n\t\t\t<factor>40</factor>\n\t\t</alt>\n\t</conversion>\n</map>',
    'SNM16ZUUB.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>SNM16ZUUB</id>\n\t<pattern>DA 00 XX XX F6 F4 XX XX E6 FC XX 00 C2 FD XX XX F2 FE XX XX DA 00 XX XX F6 F4 XX XX E6 FC XX XX C2 FD XX XX F2 FE XX XX DA 00 XX XX F6 F4 XX XX E6 FC MMXX XX</pattern>\n\t<pattern>DA 00 XX XX F6 F4 XX XX E6 FC XX XX C2 FD XX XX DA 00 XX XX F6 F4 XX XX E6 FC MMXX XX C2 FD XX XX F2 FE XX XX DA</pattern>\n\t<conversion>\n\t\t<factor>0.250000</factor>\n\t\t<endianness>LoHi</endianness>\n\t\t<width>2</width>\n\t\t<alt>\n\t\t\t<factor>40</factor>\n\t\t</alt>\n\t</conversion>\n</map>\n',
    'SRL11OPUW.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>SRL11OPUW</id>\n\t<pattern>F0 D7 DA XX XX XX F0 F4 E6 FC MMXX XX E6 FD XX XX F2 FE XX XX DA</pattern>\n\t<address>\n\t\t<offset>4</offset>\n\t</address>\n\t<conversion>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t\t<factor>0.023438</factor>\n\t</conversion>\n</map>',
    'SRL12GKUB.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>SRL12GKUB</id>\n\t<pattern>E6 FC XX XX E6 FD XX XX C2 FE XX XX D7 40 XX XX F2 FF XX XX DA XX XX XX D7 40 XX XX F6 F4 XX XX DB 00</pattern>\n\t<address>\n\t\t<offset>2</offset>\n\t\t<dppOffset>6</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.75</factor>\n\t</conversion>\n</map>',
    'SRL12GKUW.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>SRL12GKUW</id>\n\t<pattern>F6 8E XX XX E6 FC XX XX E6 FD XX XX C2 FE XX50 E6 FC MMXX</pattern>\n\t<address>\n\t\t<dppOffset>4</dppOffset>\n\t</address>\n\t<conversion>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t\t<factor>0.023438</factor>\n\t</conversion>\n</map>',
    'SRL12ZUUB.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>SRL12ZUUB</id>\n\t<pattern>E6 FC XX XX E6 FD XX XX F2 FE XX XX F2 FF XX XX DA 00 B8 78 XX18 E6 FC MMXX XX E6 FD XX XX F2 FE XX XX F2 FF XX XX DA 00 B8 78 XX70 46 F9 7F 00 XX20 46 F9 80 FF</pattern>\n\t<address>\n\t\t<offset>4</offset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.023438</factor>\n\t\t<endianness>LoHi</endianness>\n\t\t<width>2</width>\n\t</conversion>\n</map>\n',
    'TVUB.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>TVUB</id>\n\t<pattern>C2 FC XX XX E6 FD XX XX E6 FE MMXX XX C2 FF XX XX DA XX XX XX D7 XX30 FF FF</pattern>\n\t<pattern>E6 XX FF FF XX10 C2 FC XX XX E6 FD XX XX E6 FE MMXX XX C2 FF XX XX DA 00</pattern>\n\t<conversion>\n\t\t<factor>0.002667</factor>\n\t\t<endianness>LoHi</endianness>\n\t\t<width>2</width>\n\t</conversion>\n\t<rowAxis>\n\t\t<id>TVUB_AXIS</id>\n\t</rowAxis>\n</map>\n',
    'TVUB_AXIS.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>TVUB_AXIS</id>\n\t<pattern>C2 FC XX XX E6 FD XX XX E6 FE MMXX XX C2 FF XX XX DA XX XX XX D7 XX30 FF FF</pattern>\n\t<pattern>E6 XX FF FF XX10 C2 FC XX XX E6 FD XX XX E6 FE MMXX XX C2 FF XX XX DA 00</pattern>\n\t<address>\n\t\t<offset>-4</offset>\n\t</address>\n\t<conversion>\n\t\t<factor>0.070400</factor>\n\t</conversion>\n\t<length>\n\t\t<address>\n\t\t\t<offset>-8</offset>\n\t\t</address>\n\t</length>\n</map>\n',
    'VARDEF.audi.xml': '<?xml version="1.0" encoding="UTF-8"?>\n<map xmlns="http://prj-tuning.com/mapdef" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n\txsi:schemaLocation="http://prj-tuning.com/mapdef mapdef.xsd ">\n\t<id>VARDEF</id>\n\t<pattern>E1 XX F7 FA XX XX F7 8E XX20 F2 F4 MMXX XX 7C XX F7 F8 XX XX</pattern>\n\t<conversion>\n\t\t<width>2</width>\n\t\t<endianness>LoHi</endianness>\n\t</conversion>\n</map>\n',
}

@dataclass
class AddressSpec:
    offset: Optional[int]=None
    dpp_offset: Optional[int]=None
    dpp: Optional[int]=None

@dataclass
class Conversion:
    factor: float=1.0
    offset: float=0.0
    width: int=1
    endianness: str="HiLo"
    signed: bool=False
    alt: Optional["Conversion"]=None

@dataclass
class LengthSpec:
    hardcoded: Optional[int]=None
    address: Optional[AddressSpec]=None
    width: int=1

@dataclass
class Definition:
    id: str
    patterns: list[str]=field(default_factory=list)
    address: Optional[AddressSpec]=None
    conversion: Conversion=field(default_factory=Conversion)
    length: Optional[LengthSpec]=None
    row_axis_ids: list[str]=field(default_factory=list)
    col_axis_ids: list[str]=field(default_factory=list)
    after_row_axis: bool=False
    after_col_axis: bool=False

@dataclass
class Located:
    id: str=""
    address: int=0
    factor: float=1.0
    offset: float=0.0
    width: int=1
    endianness: str="HiLo"
    signed: bool=False
    length: int=0
    axis: bool=False
    pattern_location: int=0
    definition: Optional[Definition]=None
    x_axis: Optional["Located"]=None
    y_axis: Optional["Located"]=None

    @property
    def byte_length(self):
        return self.length * self.width

@dataclass
class BankComponent:
    key: str
    kind: str
    source: int
    size: int
    dest: int

@dataclass
class BankLayout:
    bank: int
    start: int
    end: int
    components: list[BankComponent]=field(default_factory=list)


def _text(el, name, default=None):
    child=el.find(f"m:{name}", NS)
    return default if child is None or child.text is None else child.text.strip()

def _bool(el, name):
    v=_text(el,name)
    return str(v).lower()=="true"

def parse_address(el):
    if el is None: return None
    def iv(name):
        t=_text(el,name)
        return None if t is None else int(t,0)
    dpp_el=el.find("m:dpp",NS)
    dpp=None
    if dpp_el is not None and dpp_el.text:
        raw=dpp_el.text.strip()
        try: dpp=int(raw,0)
        except ValueError:
            try: dpp=int(raw,16)
            except ValueError: dpp=None
    return AddressSpec(offset=iv("offset"), dpp_offset=iv("dppOffset"), dpp=dpp)

def parse_conversion(el):
    if el is None: return Conversion()
    def fv(name, default):
        t=_text(el,name)
        return default if t is None else float(t)
    def iv(name, default):
        t=_text(el,name)
        return default if t is None else int(t,0)
    alt_el=el.find("m:alt",NS)
    return Conversion(
        factor=fv("factor",1.0), offset=fv("offset",0.0), width=iv("width",1),
        endianness=_text(el,"endianness","HiLo"),
        signed=str(_text(el,"signed","false")).lower()=="true",
        alt=parse_conversion(alt_el) if alt_el is not None else None)

def parse_length(el):
    if el is None: return None
    hard=_text(el,"hardcoded")
    width=_text(el,"width")
    return LengthSpec(hardcoded=None if hard is None else int(hard,0),
                      address=parse_address(el.find("m:address",NS)),
                      width=1 if width is None else int(width,0))

def parse_definition(xml_text):
    root=ET.fromstring(xml_text)
    did=_text(root,"id")
    patterns=[p.text.strip() for p in root.findall("m:pattern",NS) if p.text]
    row=root.find("m:rowAxis",NS)
    col=root.find("m:colAxis",NS)
    row_ids=[x.text.strip() for x in row.findall("m:id",NS)] if row is not None else []
    col_ids=[x.text.strip() for x in col.findall("m:id",NS)] if col is not None else []
    return Definition(id=did, patterns=patterns,
        address=parse_address(root.find("m:address",NS)),
        conversion=parse_conversion(root.find("m:conversion",NS)),
        length=parse_length(root.find("m:length",NS)),
        row_axis_ids=row_ids, col_axis_ids=col_ids,
        after_row_axis=_bool(root,"afterRowAxis"), after_col_axis=_bool(root,"afterColAxis"))



@dataclass
class EcuEntry:
    name: str
    columns: list[str]

@dataclass
class SwitchInput:
    role: str
    ecu_name: str
    address: int
    bit: int

SWITCH_ALIASES = {
    "Clutch": ("b_kuppl", "b_kuppl_e", "b_clutch"),
    "Brake": ("b_br", "b_brems", "b_brake"),
    "Cruise SET": ("s_fgrsv", "b_fgrset", "fgr_set", "cruise_set"),
    "Cruise RES": ("s_fgrwb", "b_fgrres", "fgr_res", "cruise_res"),
}

def parse_ecu_file(path: Path) -> dict[str, EcuEntry]:
    try:
        text = path.read_text(encoding="latin-1")
    except OSError as exc:
        raise MapSwitchError(f"Cannot read ECU file: {exc}") from exc
    entries: dict[str, EcuEntry] = {}
    for raw_line in text.splitlines():
        line = raw_line.replace("\r", "")
        stripped = line.lstrip()
        if not stripped or stripped[0] in ";#/[":
            continue
        comments=[]
        def keep_comment(match):
            comments.append(match.group(1)); return f"#COMMENT{len(comments)-1}"
        line=re.sub(r"\{([^}]*)\}", keep_comment, line)
        line=line.replace("\t","").replace(" ","")
        columns=line.split(",")
        if len(columns)<10:
            continue
        for idx,comment in enumerate(comments):
            columns=[x.replace(f"#COMMENT{idx}",comment) for x in columns]
        entries[columns[0].lower()] = EcuEntry(columns[0].lower(), columns[1:])
    if not entries:
        raise MapSwitchError("ECU file contains no usable ME7Info entries")
    return entries

def ecu_address(entries: dict[str,EcuEntry], name: str) -> Optional[int]:
    e=entries.get(name.lower())
    if e is None or len(e.columns)<2: return None
    try: return int(e.columns[1].strip(),16)
    except ValueError: return None

def ecu_bit(entries: dict[str,EcuEntry], name: str) -> Optional[int]:
    e=entries.get(name.lower())
    if e is None or len(e.columns)<4: return None
    raw=e.columns[3].strip().lower()
    if raw.startswith("0x"): raw=raw[2:]
    try: mask=int(raw,16)
    except ValueError: return None
    if mask<=0 or (mask & (mask-1)): return None
    return mask.bit_length()-1


def ecu_factor(entries: dict[str,EcuEntry], name: str) -> Optional[float]:
    e=entries.get(name.lower())
    if e is None or len(e.columns)<8:
        return None
    try:
        return float(e.columns[7].strip())
    except (ValueError, TypeError):
        return None


def resolve_tach_source(entries: dict[str,EcuEntry]) -> tuple[int,float]:
    """Resolve the stock word RPM source used by the CAN/tach path.

    NefMoto's ME7.5 method hooks the stock load of nmot_w immediately before
    the two CAN-byte stores.  The .ecu definition supplies both RAM address and
    engineering factor, so no ECU-specific RPM raw values are hardcoded.
    """
    addr=ecu_address(entries,"nmot_w")
    factor=ecu_factor(entries,"nmot_w")
    if addr is None or factor is None or factor <= 0:
        raise MapSwitchError("Required .ecu symbol nmot_w with a positive factor was not found")
    if not 0 <= addr <= 0xFFFF:
        raise MapSwitchError(f"nmot_w address 0x{addr:X} is not directly addressable by the supported CAN hook")
    return addr,factor


def tach_raw_values(entries: dict[str,EcuEntry], banks:int) -> list[int]:
    _,factor=resolve_tach_source(entries)
    values=[]
    for bank in range(1,banks+1):
        rpm=bank*1000.0
        raw=int(round(rpm/factor))
        if not 0 <= raw <= 0xFFFF:
            raise MapSwitchError(f"Tach value {rpm:g} rpm does not fit nmot_w raw WORD with factor {factor:g}")
        values.append(raw)
    return values

def resolve_switch_input(entries, role, required=True):
    for name in SWITCH_ALIASES[role]:
        addr, bit = ecu_address(entries, name), ecu_bit(entries, name)
        if addr is None or bit is None:
            continue
        # ME7Info can describe the upper byte by its odd byte address.
        if addr & 1:
            addr -= 1
            bit += 8
        if bit > 15:
            continue
        _bitoff(addr)
        return SwitchInput(role, name, addr, bit)
    if required:
        raise MapSwitchError(f"Required switch input not found in .ecu: {role}")
    return None

def resolve_ecu_path(bin_path: Path, explicit: Optional[Path]) -> Path:
    if explicit is not None:
        q=explicit.expanduser().resolve()
        if not q.is_file(): raise MapSwitchError(f"ECU file not found: {q}")
        return q
    candidates=[bin_path.with_suffix('.ecu'), bin_path.with_name(bin_path.stem+'.ecu')]
    for q in candidates:
        if q.is_file(): return q.resolve()
    # Also accept a single .ecu next to the BIN for convenient testing.
    nearby=list(bin_path.parent.glob('*.ecu'))
    if len(nearby)==1: return nearby[0].resolve()
    raise MapSwitchError("No .ecu file found. Put matching <binname>.ecu next to the BIN or use --ecu FILE")

def choose_switch_mode(arg: Optional[str]) -> str:
    if arg:
        return arg

    menu_box("MAP SWITCH METHOD", [
        "",
        "  [1]  Cruise Control",
        "       Clutch + SET  →  Next bank",
        "       Clutch + RES  →  Previous bank",
        "",
        "  [2]  Brake Pedal",
        "       Hold clutch + press brake 5 times",
        "       →  Next bank",
        "",
        "  Default selection: Cruise Control",
        ""
    ])

    while True:
        raw=prompt_choice("Select switch method  [1/2]", "1").lower()
        if not raw or raw in {"1","cruise"}:
            return "cruise"
        if raw in {"2","brake5","brake"}:
            return "brake5"
        warn("Invalid selection. Enter 1 or 2.")

def resolve_switch_inputs(entries: dict[str,EcuEntry], mode: str) -> list[SwitchInput]:
    inputs=[resolve_switch_input(entries,"Clutch",True)]
    if mode=="cruise":
        inputs += [resolve_switch_input(entries,"Cruise SET",True), resolve_switch_input(entries,"Cruise RES",True)]
        # Brake is useful for diagnostics but not required by this mode.
        b=resolve_switch_input(entries,"Brake",False)
        if b: inputs.append(b)
    elif mode=="brake5":
        inputs += [resolve_switch_input(entries,"Brake",True)]
    else:
        raise MapSwitchError(f"Unsupported switch mode: {mode}")
    return [x for x in inputs if x is not None]

def print_switch_plan(mode: str, banks: int, inputs: list[SwitchInput], tach_values:list[int]):
    section("MAP SWITCH CONFIGURATION")

    print(f"  {'Banks':<20}: {banks}")
    print(f"  {'Switch mode':<20}: {'Cruise Control' if mode=='cruise' else 'Clutch + 5x Brake'}")
    print()

    print("  Inputs")
    print("  " + "─"*66)
    for sig in inputs:
        print(f"  {sig.role:<20} 0x{sig.address:06X}.{sig.bit:<2}   ({sig.ecu_name})")

    print()
    print("  Switching logic")
    print("  " + "─"*66)
    if mode=="cruise":
        print("  Clutch + SET          → Next bank")
        print("  Clutch + RES          → Previous bank")
        print(f"  Forward               → {' → '.join(str(i) for i in range(1,banks+1))} → 1")
        print(f"  Reverse               → {' → '.join(str(i) for i in range(banks,0,-1))} → {banks}")
    else:
        print("  Clutch + 5x Brake     → Next bank")
        print("  Time limit            → None")
        print(f"  Forward               → {' → '.join(str(i) for i in range(1,banks+1))} → 1")

    print()
    print("  Tachometer confirmation")
    print("  " + "─"*66)
    for i,raw in enumerate(tach_values,1):
        print(f"  Bank {i:<2}                → {i*1000:4d} rpm   (raw 0x{raw:04X})")
    print("  After timeout         → stock nmot_w to CAN")
    print()
    ok("Switch configuration ready")

def java_get_int(binary: bytes, offset: int) -> int:
    # BigInteger(new byte[]{binary[offset+1], binary[offset]}) & 0xffff
    if offset < 0 or offset+1 >= len(binary):
        raise MapSwitchError(f"16-bit read outside BIN at 0x{offset:X}")
    return ((binary[offset+1] << 8) | binary[offset]) & 0xFFFF


def _pattern_matches(tokens, binary: bytes, offset: int):
    # Literal port of PatternMatcher.patternMatches(). Return -1 for no match,
    # 0 for match without marker, or absolute marker address.
    curpos=offset
    marker=-1
    i=0
    while i < len(tokens):
        token=tokens[i]
        if token.startswith("MM"):
            token=token[2:]
            marker=curpos
        if token == "XX":
            if curpos >= len(binary): return -1
            curpos += 1; i += 1; continue
        if token.startswith("XX"):
            try: max_len=int(token[2:])
            except ValueError: raise MapSwitchError(f"Bad pattern token {token}")
            pos=curpos
            while max_len >= 0:
                if pos > len(binary): break
                result=_pattern_matches(tokens[i+1:], binary, pos)
                if result != -1:
                    return result if marker == -1 else marker
                pos += 1; max_len -= 1
            return -1
        if curpos >= len(binary): return -1
        if (int(token,16)&0xFF) != binary[curpos]: return -1
        curpos += 1; i += 1
    return 0 if marker == -1 else marker

_pattern_cache={}
def find_pattern(pattern: str, binary: bytes) -> int:
    key=(id(binary),pattern)
    if key in _pattern_cache: return _pattern_cache[key]
    tokens=pattern.split(" ")
    matchpos=0
    while matchpos < len(binary):
        result=_pattern_matches(tokens,binary,matchpos)
        if result != -1:
            if result > 0: matchpos=result
            break
        matchpos += 1
    if matchpos == len(binary): matchpos=-1
    _pattern_cache[key]=matchpos
    return matchpos


def transfer_conversion(lm: Located, conv: Conversion):
    lm.factor=conv.factor; lm.offset=conv.offset; lm.width=conv.width
    lm.endianness=conv.endianness; lm.signed=conv.signed


def get_address(binary: bytes, address: Optional[AddressSpec], pattern_location: int) -> int:
    # Exact Audi branch from Me7XmlPlugin.getAddress().
    dpp=0x204
    if address is not None:
        if address.dpp is not None: dpp=address.dpp & 0xFFFF
        if address.dpp_offset is not None:
            dpp=java_get_int(binary, pattern_location + address.dpp_offset)
    if address is not None and address.offset is not None:
        addr=java_get_int(binary, pattern_location + address.offset)
    else:
        addr=java_get_int(binary, pattern_location)
    return dpp * 0x4000 - 0x800000 + addr


def get_located_map(defn: Definition, binary: bytes):
    lm=Located(definition=defn)
    pattern_location=0
    if defn.patterns:
        found=-1
        for pattern in defn.patterns:
            found=find_pattern(pattern,binary)
            if found != -1: break
        if found == -1: return None
        pattern_location=found
        lm.address=get_address(binary,defn.address,pattern_location)
    lm.id=defn.id
    lm.pattern_location=pattern_location
    transfer_conversion(lm,defn.conversion)
    if defn.length is not None:
        ls=defn.length
        if ls.hardcoded is not None:
            lm.length=ls.hardcoded
        else:
            len_addr=get_address(binary,ls.address,pattern_location)
            lm.length=(binary[len_addr]&0xFF) if ls.width==1 else java_get_int(binary,len_addr)
    return lm


def set_axis(lm: Located, binary: bytes):
    # Literal LocatedMapWithXml.setAxis().
    if lm.axis: return
    lm.axis=True
    d=lm.definition
    if d.length is None:
        lm.length=(binary[lm.address]&0xFF) if lm.width==1 else java_get_int(binary,lm.address)
        if lm.width==2 and lm.length>255:
            lm.width=1
            lm.length=binary[lm.address]&0xFF
            if d.conversion.alt is not None:
                transfer_conversion(lm,d.conversion.alt)
        lm.address += lm.width


def load_definitions(xml_dir: Optional[Path] = None):
    """Load every Audi XML definition. Duplicate IDs are kept as variants.

    With no directory, use the complete Audi XML set embedded from the supplied
    me7-tools ZIP.  --xml-dir can point at an updated Audi XML folder.
    """
    items=[]
    if xml_dir is None:
        for name, xml in EMBEDDED_XML.items():
            items.append((name, parse_definition(xml)))
    else:
        if not xml_dir.is_dir():
            raise MapSwitchError(f"XML directory not found: {xml_dir}")
        for p in sorted(xml_dir.rglob('*.audi.xml')):
            items.append((p.name, parse_definition(p.read_text(encoding='utf-8'))))
    return items


def locate_maps(binary: bytes, xml_dir: Optional[Path] = None):
    global _pattern_cache
    _pattern_cache={}
    defs=load_definitions(xml_dir)
    axis_ids=set()
    for _, d in defs:
        axis_ids.update(d.col_axis_ids); axis_ids.update(d.row_axis_ids)

    # Keep all successful XML variants.  The original Java project stores Map
    # objects in a HashSet and later indexes by id; keeping variants explicitly
    # makes duplicate ids (KRKTE/KRKTE_8) deterministic without inventing any
    # new locator rule.
    variants={}
    for filename, d in defs:
        lm=get_located_map(d,binary)
        if lm is not None:
            variants.setdefault(lm.id, []).append((filename, lm))

    # Select the first successful XML variant for each id in file-name order.
    maps={mid: vals[0][1] for mid, vals in variants.items()}
    sources={mid: vals[0][0] for mid, vals in variants.items()}

    # Literal Me7XmlPlugin axis-linking phase.
    for mid in list(maps.keys()):
        if mid not in maps: continue
        lm=maps[mid]; d=lm.definition
        if lm.id in axis_ids:
            set_axis(lm,binary)
        if d.col_axis_ids:
            for aid in d.col_axis_ids:
                ax=maps.get(aid)
                if ax is not None:
                    lm.x_axis=ax; set_axis(ax,binary)
                    if d.after_col_axis:
                        lm.address=ax.address + ax.width*ax.length
                    break
            if lm.x_axis is None:
                maps.pop(mid,None); sources.pop(mid,None); continue
        if d.row_axis_ids:
            for aid in d.row_axis_ids:
                ay=maps.get(aid)
                if ay is not None:
                    lm.y_axis=ay; set_axis(ay,binary)
                    if d.after_row_axis:
                        lm.address=ay.address + ay.width*ay.length
                    break
            if lm.y_axis is None:
                maps.pop(mid,None); sources.pop(mid,None); continue

    # Payload length is not supplied for most maps by the Java XML plugin; for
    # bank copying derive it only from the axes exactly attached above.
    for mid in REQUIRED_MAPS:
        lm=maps.get(mid)
        if lm is None: continue
        if lm.x_axis is not None and lm.y_axis is not None:
            lm.length=lm.x_axis.length*lm.y_axis.length
        elif lm.x_axis is not None:
            lm.length=lm.x_axis.length
        elif lm.y_axis is not None:
            lm.length=lm.y_axis.length
        elif lm.length==0:
            lm.length=1
    return maps, sources, variants



# ---------------------------------------------------------------------------
# XML-driven relaxed fallback
# ---------------------------------------------------------------------------
# Some ME7.5 software versions insert/omit C167 setup instructions around the
# same map lookup. The original Java PatternMatcher only accepts the exact XML
# byte sequence. This fallback still uses the supplied XML as the definition:
# fixed bytes stay fixed, XX stays one wildcard byte, XXnn stays a bounded gap,
# MM still marks the address operand, and the same DPP/address math is used.
# It only tolerates a small number of inserted bytes / fixed-byte differences.

from functools import lru_cache


def _relaxed_tokens(pattern: str):
    out=[]
    for raw in pattern.split():
        marker=False
        token=raw
        if token.startswith("MM"):
            marker=True
            token=token[2:]
        if token == "XX":
            out.append(("wild", None, marker))
        elif token.startswith("XX"):
            out.append(("gap", int(token[2:]), marker))
        else:
            out.append(("byte", int(token,16)&0xFF, marker))
    return out


def _fixed_runs(tokens):
    runs=[]; cur=[]; start=None
    for i,(kind,value,marker) in enumerate(tokens):
        if kind == "byte":
            if start is None: start=i
            cur.append(value)
        else:
            if len(cur)>=3: runs.append((len(cur),start,i,bytes(cur)))
            cur=[]; start=None
    if len(cur)>=3: runs.append((len(cur),start,len(tokens),bytes(cur)))
    return sorted(runs, key=lambda x:(-x[0],x[1]))


def _offset_range_to_token(tokens, index):
    lo=hi=0
    for kind,value,marker in tokens[:index]:
        if kind in ("byte","wild"):
            lo += 1; hi += 1
        else:
            hi += value
    return lo,hi


def _relaxed_match_at(tokens, binary: bytes, start: int,
                      max_insertions: int, max_mismatches: int):
    n=len(binary)
    @lru_cache(maxsize=None)
    def rec(ti,pos,ins,mis,marker):
        if ti == len(tokens):
            return (mis,ins,marker,pos)
        if pos > n: return None
        kind,value,is_marker=tokens[ti]
        next_marker = pos if is_marker else marker
        best=None
        if kind == "gap":
            for gap in range(value+1):
                if pos+gap>n: break
                r=rec(ti+1,pos+gap,ins,mis,next_marker)
                if r is not None and (best is None or r[:2] < best[:2]): best=r
            return best
        # Optional bytes inserted by another C167 code-layout variant.
        if ins < max_insertions and pos < n:
            r=rec(ti,pos+1,ins+1,mis,marker)
            if r is not None: best=r
        if pos>=n: return best
        add=0
        if kind == "byte" and binary[pos] != value: add=1
        if mis+add <= max_mismatches:
            r=rec(ti+1,pos+1,ins,mis+add,next_marker)
            if r is not None and (best is None or r[:2] < best[:2]): best=r
        return best
    return rec(0,start,0,0,-1)


def find_pattern_relaxed(pattern: str, binary: bytes,
                         max_insertions: int=8, max_mismatches: int=3,
                         result_limit: int=80):
    tokens=_relaxed_tokens(pattern)
    starts=set(); results=[]
    # Anchor only on literal runs from the supplied XML. No ECU address is used.
    for run_len, token_start, token_end, anchor in _fixed_runs(tokens):
        positions=[]; pos=0
        while True:
            hit=binary.find(anchor,pos)
            if hit<0: break
            positions.append(hit); pos=hit+1
            if len(positions)>3000: break
        if not positions: continue
        lo,hi=_offset_range_to_token(tokens,token_start)
        for hit in positions:
            for off in range(lo,hi+1):
                for inserted_before in range(max_insertions+1):
                    start=hit-off-inserted_before
                    if start<0 or start in starts: continue
                    starts.add(start)
                    r=_relaxed_match_at(tokens,binary,start,max_insertions,max_mismatches)
                    if r is None: continue
                    mis,ins,marker,end=r
                    if marker<0: marker=start
                    results.append((mis,ins,-run_len,start,marker,end))
                    if len(results)>=result_limit*10: break
                if len(results)>=result_limit*10: break
            if len(results)>=result_limit*10: break
    results.sort()
    unique=[]; seen=set()
    for item in results:
        marker=item[4]
        if marker in seen: continue
        seen.add(marker); unique.append(item)
        if len(unique)>=result_limit: break
    return unique


def _definition_relaxed_candidates(filename: str, d: Definition, binary: bytes):
    candidates=[]
    for pattern in d.patterns:
        for score in find_pattern_relaxed(pattern,binary):
            mis,ins,neg_anchor,start,marker,end=score
            try:
                lm=Located(definition=d)
                lm.id=d.id; lm.pattern_location=marker
                lm.address=get_address(binary,d.address,marker)
                transfer_conversion(lm,d.conversion)
                if d.length is not None:
                    if d.length.hardcoded is not None:
                        lm.length=d.length.hardcoded
                    else:
                        la=get_address(binary,d.length.address,marker)
                        if not 0<=la<len(binary): continue
                        lm.length=(binary[la]&0xFF) if d.length.width==1 else java_get_int(binary,la)
                if not 0 <= lm.address < len(binary): continue
                # All MultiMap calibration items must resolve into the flash image.
                candidates.append((mis,ins,filename,lm))
            except (MapSwitchError,IndexError,ValueError):
                continue
    candidates.sort(key=lambda x:(x[0],x[1],x[3].address))
    # Deduplicate decoded addresses.
    out=[]; seen=set()
    for x in candidates:
        if x[3].address in seen: continue
        seen.add(x[3].address); out.append(x)
    return out


def _axis_quality(lm: Located, binary: bytes):
    """Generic sanity score derived from XML conversion, not ECU addresses."""
    try:
        original_width=lm.width
        test=Located(**{**lm.__dict__})
        set_axis(test,binary)
    except Exception:
        return -10000
    # Java has a generic 16->8 bit fallback for axis formats with an <alt>
    # conversion.  Do not accept that downgrade when this XML has no alt
    # conversion: for LDRXN/LDRXNZK it otherwise turns random WORD data into
    # a plausible-looking 8 point axis.
    if original_width == 2 and test.width == 1 and lm.definition.conversion.alt is None:
        return -10000
    if not (1 <= test.length <= 64): return -10000
    if test.address < 0 or test.address + test.length*test.width > len(binary): return -10000
    vals=[]
    for i in range(test.length):
        pos=test.address+i*test.width
        if test.width==1: raw=binary[pos]
        else: raw=java_get_int(binary,pos)
        vals.append(raw*test.factor+test.offset)
    score=20
    if len(vals)>1:
        nondec=sum(vals[i]<=vals[i+1] for i in range(len(vals)-1))
        score += int(30*nondec/(len(vals)-1))
    if max(vals,default=0)>min(vals,default=0): score+=10
    return score


def add_relaxed_missing(binary: bytes, defs, maps, sources):
    """Fill only definitions that exact Java/XML lookup did not produce."""
    by_id={}
    for filename,d in defs:
        by_id.setdefault(d.id,[]).append((filename,d))

    # First recover missing axis IDs referenced by the six requested maps.
    wanted_axis=set()
    for req in REQUIRED_MAPS:
        for _,d in by_id.get(req,[]):
            wanted_axis.update(d.row_axis_ids); wanted_axis.update(d.col_axis_ids)

    recovered={}
    for aid in sorted(wanted_axis):
        if aid in maps: continue
        pool=[]
        for filename,d in by_id.get(aid,[]):
            for mis,ins,src,lm in _definition_relaxed_candidates(filename,d,binary):
                q=_axis_quality(lm,binary)
                if q>-10000: pool.append((mis,ins,-q,lm.address,src,lm))
        pool.sort(key=lambda x:x[:5])
        if pool:
            recovered[aid]=pool

    # LDRXN/LDRXNZK need separate axes. Pick the best distinct pair if possible.
    ldr_ids=("LDRXN_ROWAXIS","LDRXNZK_ROWAXIS")
    if all(x not in maps and x in recovered for x in ldr_ids):
        best=None
        for a in recovered[ldr_ids[0]][:12]:
            for b in recovered[ldr_ids[1]][:12]:
                if a[5].address==b[5].address: continue
                key=(a[0]+b[0],a[1]+b[1],a[2]+b[2])
                if best is None or key<best[0]: best=(key,a,b)
        if best:
            for aid,item in zip(ldr_ids,best[1:]):
                lm=item[5]; set_axis(lm,binary); maps[aid]=lm; sources[aid]=item[4]+" [relaxed XML]"
    for aid,pool in recovered.items():
        if aid in maps: continue
        item=pool[0]; lm=item[5]; set_axis(lm,binary)
        maps[aid]=lm; sources[aid]=item[4]+" [relaxed XML]"

    # Recover requested map definitions with patterns (notably KFZW2).
    for req in REQUIRED_MAPS:
        if req in maps: continue
        candidates=[]
        for filename,d in by_id.get(req,[]):
            if not d.patterns: continue
            for mis,ins,src,lm in _definition_relaxed_candidates(filename,d,binary):
                # Prefer a different address from its sibling ignition map.
                penalty=0
                if req=="KFZW2" and "KFZW" in maps and lm.address==maps["KFZW"].address:
                    penalty+=100
                candidates.append((mis,ins,penalty,lm.address,src,lm))
        candidates.sort(key=lambda x:x[:5])
        if candidates:
            x=candidates[0]; maps[req]=x[5]; sources[req]=x[4]+" [relaxed XML]"

    # Re-create after-axis maps from their XML definitions once axes exist.
    for req in REQUIRED_MAPS:
        if req in maps: continue
        for filename,d in by_id.get(req,[]):
            if d.patterns: continue
            lm=Located(id=d.id,definition=d); transfer_conversion(lm,d.conversion)
            if d.col_axis_ids:
                for aid in d.col_axis_ids:
                    if aid in maps:
                        lm.x_axis=maps[aid]
                        if d.after_col_axis: lm.address=lm.x_axis.address+lm.x_axis.width*lm.x_axis.length
                        break
            if d.row_axis_ids:
                for aid in d.row_axis_ids:
                    if aid in maps:
                        lm.y_axis=maps[aid]
                        if d.after_row_axis: lm.address=lm.y_axis.address+lm.y_axis.width*lm.y_axis.length
                        break
            if (not d.col_axis_ids or lm.x_axis) and (not d.row_axis_ids or lm.y_axis):
                maps[req]=lm; sources[req]=filename+" [after-axis XML]"; break

    # Attach axes to all requested maps after recovery.
    for req in REQUIRED_MAPS:
        lm=maps.get(req)
        if lm is None or lm.definition is None: continue
        d=lm.definition
        if lm.x_axis is None:
            for aid in d.col_axis_ids:
                if aid in maps: lm.x_axis=maps[aid]; break
        if lm.y_axis is None:
            for aid in d.row_axis_ids:
                if aid in maps: lm.y_axis=maps[aid]; break
        if d.after_col_axis and lm.x_axis:
            lm.address=lm.x_axis.address+lm.x_axis.width*lm.x_axis.length
        if d.after_row_axis and lm.y_axis:
            lm.address=lm.y_axis.address+lm.y_axis.width*lm.y_axis.length
        if lm.x_axis and lm.y_axis: lm.length=lm.x_axis.length*lm.y_axis.length
        elif lm.x_axis: lm.length=lm.x_axis.length
        elif lm.y_axis: lm.length=lm.y_axis.length
        elif lm.length==0: lm.length=1
    return maps,sources


# ---------------------------------------------------------------------------
# Strong C167 structural fallback for the two map pairs used by MultiMap.
# No ECU-specific addresses are used. The geometry comes from the supplied
# Audi XML definitions: KFZW/KFZW2 are 8-bit maps sharing a 12x16 layout, and
# LDRXN/LDRXNZK are 16-point WORD maps placed directly after their row axes.
# ---------------------------------------------------------------------------
import re


def _xml_def_by_id(defs, wanted: str) -> Definition:
    for _, d in defs:
        if d.id == wanted:
            return d
    raise MapSwitchError(f"XML definition not found: {wanted}")


def _linear_from_dpp_word(word: int, dpp: int = 0x204) -> int:
    return dpp * 0x4000 - 0x800000 + word


def _payload_not_blank(binary: bytes, addr: int, size: int) -> bool:
    if addr < 0 or addr + size > len(binary):
        return False
    block = binary[addr:addr+size]
    return block not in (b"\x00" * size, b"\xFF" * size) and len(set(block)) > 3


def locate_ignition_pair_structural(binary: bytes, defs):
    """Locate KFZW/KFZW2 from the paired Bosch ignition-map lookup routine.

    The supplied KFZW XML itself describes two consecutive calls to the same
    map lookup.  Some ME7.5 variants use a larger instruction gap than the old
    Java XX18 wildcard accepts.  Instead of fuzzy matching arbitrary bytes,
    extract the two E6 FC map operands from that same C167 routine and require
    the two resolved calibration tables to be exactly one 12x16 BYTE table
    apart (0xC0 bytes).  This works without any software-specific address.
    """
    # Two map pointer setup/call groups. E6 FD and the lookup target may vary.
    pat = re.compile(
        rb"\xE6\xFC(..)\xE6\xFD(..)\xF2\xFE..\xF2\xFF..\xDA\x00\xB8\x78"
        rb".{0,96}?"
        rb"\xE6\xFC(..)\xE6\xFD(..)\xF2\xFE..\xF2\xFF..\xDA\x00\xB8\x78",
        re.S,
    )
    pairs = []
    for m in pat.finditer(binary):
        # KFZW XML has no address/dpp override: Audi default DPP 0x204 and the
        # E6 FC immediate is the map address operand.
        a_first = _linear_from_dpp_word(int.from_bytes(m.group(1), "little"))
        a_second = _linear_from_dpp_word(int.from_bytes(m.group(3), "little"))
        if abs(a_first - a_second) != 12 * 16:
            continue
        if not (_payload_not_blank(binary, a_first, 192) and _payload_not_blank(binary, a_second, 192)):
            continue
        # The same pair may be seen from overlapping control-flow paths.
        key = tuple(sorted((a_first, a_second)))
        if key not in [x[0] for x in pairs]:
            pairs.append((key, m.start(), a_first, a_second))

    if len(pairs) != 1:
        return None

    _, code_at, first, second = pairs[0]
    kfzw_def = _xml_def_by_id(defs, "KFZW")
    kfzw2_def = _xml_def_by_id(defs, "KFZW2")

    # Across the Bosch routine the first pointer is the variant-2 table and
    # the second pointer is the primary KFZW table.  This is register-flow
    # order, not a flash address assumption.
    kfzw = Located(id="KFZW", address=second, definition=kfzw_def,
                   pattern_location=code_at, length=192)
    transfer_conversion(kfzw, kfzw_def.conversion)
    kfzw2 = Located(id="KFZW2", address=first, definition=kfzw2_def,
                    pattern_location=code_at, length=192)
    transfer_conversion(kfzw2, kfzw2_def.conversion)
    return kfzw, kfzw2, code_at


def locate_ldr_pair_structural(binary: bytes, defs):
    """Locate LDRXN/LDRXNZK row-axis + map blocks from the paired lookup.

    XML says both maps are WORD, afterRowAxis=true.  A valid block therefore
    has a 16-bit axis length, 16 WORD axis entries, then 16 WORD map entries.
    Two consecutive blocks are 0x42 bytes apart (2 + 32 + 32).
    """
    pat = re.compile(
        rb"\xE6\xFC(..)\xE6\xFD(..)\xF2\xFE..\xDA\x82(..)"
        rb"\xF0\x94\x0D."
        rb"\xE6\xFC(..)\xE6\xFD(..)\xF2\xFE..\xDA\x82(..)",
        re.S,
    )
    candidates=[]
    for m in pat.finditer(binary):
        if m.group(3) != m.group(6):
            continue
        off1=int.from_bytes(m.group(1),"little"); dpp1=int.from_bytes(m.group(2),"little")
        off2=int.from_bytes(m.group(4),"little"); dpp2=int.from_bytes(m.group(5),"little")
        a1=dpp1*0x4000-0x800000+off1
        a2=dpp2*0x4000-0x800000+off2
        if not (0 <= a1+66 <= len(binary) and 0 <= a2+66 <= len(binary)):
            continue
        if java_get_int(binary,a1) != 16 or java_get_int(binary,a2) != 16:
            continue
        if abs(a1-a2) != 0x42:
            continue
        # Axis and map payloads must contain actual calibration data.
        if not (_payload_not_blank(binary,a1+2,32) and _payload_not_blank(binary,a1+34,32)
                and _payload_not_blank(binary,a2+2,32) and _payload_not_blank(binary,a2+34,32)):
            continue
        candidates.append((m.start(),a1,a2))

    # Dedupe same decoded pair.
    uniq=[]; seen=set()
    for x in candidates:
        key=tuple(sorted(x[1:]))
        if key in seen: continue
        seen.add(key); uniq.append(x)
    if len(uniq) != 1:
        return None

    code_at, first_axis_raw, second_axis_raw = uniq[0]
    # Register-flow order is LDRXNZK first, LDRXN second in this paired routine.
    ldrxzk_axis_raw = first_axis_raw
    ldrxn_axis_raw = second_axis_raw

    axn_def=_xml_def_by_id(defs,"LDRXN_ROWAXIS")
    axz_def=_xml_def_by_id(defs,"LDRXNZK_ROWAXIS")
    mn_def=_xml_def_by_id(defs,"LDRXN")
    mz_def=_xml_def_by_id(defs,"LDRXNZK")

    axn=Located(id="LDRXN_ROWAXIS",address=ldrxn_axis_raw,definition=axn_def,pattern_location=code_at)
    transfer_conversion(axn,axn_def.conversion); set_axis(axn,binary)
    axz=Located(id="LDRXNZK_ROWAXIS",address=ldrxzk_axis_raw,definition=axz_def,pattern_location=code_at)
    transfer_conversion(axz,axz_def.conversion); set_axis(axz,binary)

    mn=Located(id="LDRXN",definition=mn_def,address=axn.address+axn.width*axn.length,
               y_axis=axn,pattern_location=code_at,length=axn.length)
    transfer_conversion(mn,mn_def.conversion)
    mz=Located(id="LDRXNZK",definition=mz_def,address=axz.address+axz.width*axz.length,
               y_axis=axz,pattern_location=code_at,length=axz.length)
    transfer_conversion(mz,mz_def.conversion)
    return mn,mz,axn,axz,code_at



def _axis_values_word(binary: bytes, data_addr: int, count: int):
    if data_addr < 0 or data_addr + count * 2 > len(binary):
        return None
    return [java_get_int(binary, data_addr + i * 2) for i in range(count)]


def _monotonic_axis(values):
    if not values or len(set(values)) < max(3, len(values) // 2):
        return False
    return all(values[i] <= values[i + 1] for i in range(len(values) - 1))


def _find_embedded_word_axis(binary: bytes, count: int, factor: float,
                             near: int | None = None, radius: int = 0x400):
    """Find an ME7 EPROM axis stored as WORD length + WORD payload.

    This mirrors Me7XmlPlugin.setAxis(): when an axis XML has no explicit
    <length>, Java reads the axis length at the located address and then
    advances the public axis address by its width.  We therefore search for
    the generic structure [count WORD][count x WORD values], never for a
    software-specific flash address.
    """
    lo = 0x10000
    hi = 0x20000
    if near is not None:
        lo = max(lo, near - radius)
        hi = min(hi, near + radius)
    candidates = []
    for raw in range(lo, max(lo, hi - (count + 1) * 2 + 1)):
        if java_get_int(binary, raw) != count:
            continue
        data_addr = raw + 2
        values = _axis_values_word(binary, data_addr, count)
        if values is None or not _monotonic_axis(values):
            continue
        physical = [v * factor for v in values]
        # Generic calibration-axis sanity only; no ECU/software addresses.
        if not all(0 <= x <= 20000 for x in physical):
            continue
        if physical[-1] <= physical[0]:
            continue
        distance = abs(data_addr - near) if near is not None else 0
        candidates.append((distance, data_addr, values))
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates


def locate_ignition_axes_structural(binary, defs, maps):
    """Use both real axis producers, never the first nearby monotonic table."""
    pair = locate_ignition_pair_structural(binary, defs)
    if pair is None:
        return None, None
    groups = _verified_ignition_groups(binary, pair[2])
    if len(groups) != 4:
        return None, None
    inputs = {(m.group(2),m.group(3),m.group(4)) for m in groups}
    if len(inputs) != 1:
        return None, None
    header_word, y_result, x_result = next(iter(inputs))
    axes = []
    for name, result, count in [('SRL12ZUUB',x_result,12),('SNM16ZUUB',y_result,16)]:
        pattern = re.compile(rb'\xE6\xFC(..)\xF2\xFD(..)\xF2\xFE' + re.escape(result) + rb'\xDA\x00(..)\xF6\xF4' + re.escape(result),re.S)
        hits = [m for m in pattern.finditer(binary) if not m.start() & 1]
        if len(hits) != 1:
            return None,None
        hit = hits[0]
        if name=='SRL12ZUUB' and hit.group(1)!=header_word:
            return None,None
        raw = _linear_from_dpp_word(int.from_bytes(hit.group(1),'little'))
        if not 0 <= raw < len(binary)-2 or java_get_int(binary,raw)!=count:
            return None,None
        d = _xml_def_by_id(defs,name)
        ax = Located(id=name,address=raw,definition=d,pattern_location=hit.start()+2)
        transfer_conversion(ax,d.conversion);set_axis(ax,binary)
        values = _axis_values_word(binary,ax.address,count)
        if ax.width!=2 or not _monotonic_axis(values):
            return None,None
        axes.append(ax)
    return tuple(axes)


def apply_strong_structural_pairs(binary: bytes, defs, maps, sources):
    ign=locate_ignition_pair_structural(binary,defs)
    if ign is not None:
        k1,k2,code_at=ign
        x_axis, y_axis = locate_ignition_axes_structural(binary, defs, maps)
        if x_axis is None or y_axis is None:
            maps.pop("KFZW",None); maps.pop("KFZW2",None)
            sources.pop("KFZW",None); sources.pop("KFZW2",None)
        else:
            # KFZW and KFZW2 share the same XML-defined 12x16 axes.
            k1.x_axis=x_axis; k1.y_axis=y_axis; k1.length=x_axis.length*y_axis.length
            k2.x_axis=x_axis; k2.y_axis=y_axis; k2.length=x_axis.length*y_axis.length
            maps["SRL12ZUUB"]=x_axis; maps["SNM16ZUUB"]=y_axis
            maps["KFZW"]=k1; maps["KFZW2"]=k2
            sources["KFZW"]=f"KFZW.audi.xml [paired C167 @0x{code_at:06X}; shared XML axes]"
            sources["KFZW2"]=f"KFZW2.audi.xml [paired C167 @0x{code_at:06X}; shared XML axes]"
    else:
        maps.pop("KFZW",None); maps.pop("KFZW2",None)
        sources.pop("KFZW",None); sources.pop("KFZW2",None)

    ldr=locate_ldr_pair_structural(binary,defs)
    if ldr is not None:
        n,z,an,az,code_at=ldr
        maps["LDRXN_ROWAXIS"]=an; maps["LDRXNZK_ROWAXIS"]=az
        maps["LDRXN"]=n; maps["LDRXNZK"]=z
        sources["LDRXN"]=f"LDRXN.audi.xml [paired C167 @0x{code_at:06X}]"
        sources["LDRXNZK"]=f"LDRXNZK.audi.xml [paired C167 @0x{code_at:06X}]"
    else:
        maps.pop("LDRXN",None); maps.pop("LDRXNZK",None)
        sources.pop("LDRXN",None); sources.pop("LDRXNZK",None)
    return maps,sources

# Experimental KFZWMN/KFNWEGM locator.  Detection logic is intentionally
# copied only from Pops&Bangs CMD; no Pops&Bangs modifications are imported.
KFNWEGM_PATTERN_1 = bytes([128] * 30 + [26])
KFNWEGM_PATTERN_2 = bytes([
    128,128,126,125,124,123,145,141,
    128,128,125,124,122,121,148,145,
    128,128,125,124,122,121,149,146,
    128,128,125,124,122,121,26,
])

def _exp_find_last(data: bytes, pattern: bytes):
    """Return the last raw pattern start and number of matches.

    Important: this helper returns the MATCH START, not an ECU/map address.
    Callers derive the map address dynamically from the matched structure.
    """
    last=None; start=0; count=0
    while True:
        pos=data.find(pattern,start)
        if pos < 0:
            return last, count
        last=pos; count+=1; start=pos+1

def _exp_kfnwegm_start_from_match(match_start: int, pattern: bytes) -> int:
    """Derive KFNWEGM Z start from the detected pattern.

    KFNWEGM begins immediately after the dynamically located Pops&Bangs
    search pattern.
    No ECU-specific KFNWEGM address is hard-coded here.
    """
    return match_start + len(pattern)

def _exp_detect_kfzwmn(data: bytes):
    """Accept a calibration candidate only when its complete lookup agrees."""
    candidates=[]
    for match in re.finditer(rb'\x10\x0C[\x0B-\x1D]',data):
        raw=match.start(); address=raw+30
        if address+192>len(data) or data[raw+193]<=128:
            continue
        try:
            _find_kfzwmn_nearptr_hook(data,address)
            _pops_axis_payload(data,raw+2,16)
            _pops_axis_payload(data,raw+18,12)
        except MapSwitchError:
            continue
        candidates.append(address)
    if len(candidates)>1:
        raise MapSwitchError('KFZWMN: multiple calibration/lookup candidates')
    return (candidates[0] if candidates else None),len(candidates)

def add_experimental_pops_maps(binary: bytes, maps: dict, sources: dict):
    address,count=_exp_detect_kfzwmn(binary)
    if address is not None:
        hook=_find_kfzwmn_nearptr_hook(binary,address)
        y=Located(id='KFZWMN_Y',address=address-28,factor=40.0,width=1,length=16,axis=True)
        x=Located(id='KFZWMN_X',address=address-12,factor=0.75,width=1,length=12,axis=True)
        maps['KFZWMN']=Located(id='KFZWMN',address=address,factor=0.75,width=1,
                              signed=True,length=192,x_axis=x,y_axis=y,pattern_location=hook[0])
        sources['KFZWMN']=f'Pops detector + unique complete C167 lookup @0x{hook[0]:06X}'

    candidates={}
    for pattern in (KFNWEGM_PATTERN_1,KFNWEGM_PATTERN_2):
        pos=0
        while True:
            pos=binary.find(pattern,pos)
            if pos<0:
                break
            start=_exp_kfnwegm_start_from_match(pos,pattern)
            pos+=1
            if start+112>len(binary):
                continue
            try:
                hooks=[_find_kfnwegm_lookup_hook(binary,start+delta) for delta in (0,40,80)]
            except MapSwitchError:
                continue
            candidates[start]=hooks
    if len(candidates)>1:
        raise MapSwitchError('Pops tables: multiple complete calibration/lookup groups')
    if not candidates:
        return maps,sources
    start,hooks=next(iter(candidates.items()))
    for name,delta,cols,rows,hook in zip(
            ('KFNWEGM','KFTVSA','KFTVSAKAT'),(0,40,80),(8,8,4),(5,5,8),hooks):
        setup=hook[0]
        header=(java_get_int(binary,setup+14)<<14)+(java_get_int(binary,setup+10)&0x3FFF)-0x800000
        if not 0<=header<header+cols+1<=len(binary) or binary[header]!=cols:
            raise MapSwitchError(f'{name}: column header does not match the copied geometry')
        xaddr=_pops_axis_payload(binary,header+1,cols)
        yaddr=_pops_row_axis(binary,setup,rows)
        meta=DR_XDF_META[name]
        x=Located(id=name+'_X',address=xaddr,factor=meta['x']['factor'],offset=meta['x']['offset'],
                  width=1,length=cols,axis=True)
        y=Located(id=name+'_Y',address=yaddr,factor=meta['y']['factor'],offset=meta['y']['offset'],
                  width=1,length=rows,axis=True)
        maps[name]=Located(id=name,address=start+delta,factor=meta['z']['factor'],width=1,
                           length=cols*rows,x_axis=x,y_axis=y,pattern_location=setup)
        sources[name]=f'Pops layout + unique C167 lookup @0x{setup:06X}; executable axis references'
    return maps,sources

def fmt(lm):
    return f"width={lm.width*8}-bit {'signed' if lm.signed else 'unsigned'} factor={lm.factor:g} offset={lm.offset:g} endian={lm.endianness}"

def print_axis(label, ax):
    if ax is None: print(f"       {label:<6}: -"); return
    print(f"       {label:<6}: 0x{ax.address:06X} points={ax.length:<3} {fmt(ax)}")

def print_report(maps, sources=None):
    section("MAP DETECTION")
    print("  Required MultiMap calibrations")
    print("  " + "─"*66)
    print("  LAMFA  •  KFZW  •  KFZW2  •  LDRXN  •  LDRXNZK  •  KRKTE")
    print("  KFZWMN  •  KFNWEGM  •  KFTVSA  •  KFTVSAKAT (runtime switched)")
    print()

    found=0
    for name in REQUIRED_MAPS + EXPERIMENTAL_COPY_MAPS:
        m=maps.get(name)
        if m is None:
            print(f"  {_tag('[FAIL]',_RED)} {name:<9}  Not found")
            continue

        found += 1
        if m.x_axis and m.y_axis:
            shape=f"{m.x_axis.length}x{m.y_axis.length}"
        elif m.x_axis:
            shape=str(m.x_axis.length)
        elif m.y_axis:
            shape=str(m.y_axis.length)
        else:
            shape="1"

        print(f"  {_tag('[ OK ]',_GREEN)} {name:<9}  0x{m.address:06X}   {shape:<7}   {m.byte_length:>4} B")
        axes=[]
        if m.x_axis:
            axes.append(f"X 0x{m.x_axis.address:06X} ({m.x_axis.length} pts)")
        if m.y_axis:
            axes.append(f"Y 0x{m.y_axis.address:06X} ({m.y_axis.length} pts)")
        if axes:
            print(f"         {'   '.join(axes)}")

    print()
    print("  " + "─"*66)
    core_found=sum(1 for n in REQUIRED_MAPS if n in maps)
    if core_found == len(REQUIRED_MAPS):
        ok(f"All required core maps found ({core_found}/{len(REQUIRED_MAPS)})")
    else:
        warn(f"Required core maps found: {core_found}/{len(REQUIRED_MAPS)}")
    exp_found=sum(1 for n in EXPERIMENTAL_COPY_MAPS if n in maps)
    info(f"Runtime Pops maps found: {exp_found}/{len(EXPERIMENTAL_COPY_MAPS)}")

def align_up(v,a=ALIGNMENT): return (v+a-1)&~(a-1)
def find_ff_hole(data: Sequence[int], size:int, start:int, end=None, alignment=ALIGNMENT):
    if end is None or end>len(data): end=len(data)
    run=None
    for i in range(max(0,start),end):
        if data[i]==0xFF:
            if run is None: run=i
        else:
            if run is not None:
                a=align_up(run,alignment)
                if a+size<=i: return a
            run=None
    if run is not None:
        a=align_up(run,alignment)
        if a+size<=end: return a
    return None



def selector_components(maps):
    """Return selector-compatible source ranges.

    Unlike the older payload-only layout, this preserves Bosch lookup headers
    when the executable code points at the header rather than the axis payload.
    """
    out=[]
    seen=set()

    def add_raw(key, kind, source, size, alignment=1):
        sig=(source,size)
        if sig in seen:
            return
        seen.add(sig)
        out.append((key,kind,source,size,alignment))

    lam=maps["LAMFA"]
    # LAMFA Y lookup uses the 2-byte header immediately before Y payload.
    if lam.y_axis:
        add_raw("LAMFA.Y+HDR","axis-y+hdr",lam.y_axis.address-2,2+lam.y_axis.byte_length,1)
    if lam.x_axis:
        add_raw("LAMFA.X","axis-x",lam.x_axis.address,lam.x_axis.byte_length,max(1,min(lam.x_axis.width,ALIGNMENT)))
    add_raw("LAMFA","map",lam.address,lam.byte_length,max(1,min(lam.width,ALIGNMENT)))

    # KFZW/KFZW2 share one validated axis set.
    k1=maps["KFZW"]; k2=maps["KFZW2"]
    if k1.x_axis:
        add_raw("KFZW.X","axis-x",k1.x_axis.address,k1.x_axis.byte_length,max(1,min(k1.x_axis.width,ALIGNMENT)))
    if k1.y_axis:
        add_raw("KFZW.Y","axis-y",k1.y_axis.address,k1.y_axis.byte_length,max(1,min(k1.y_axis.width,ALIGNMENT)))
    add_raw("KFZW","map",k1.address,k1.byte_length,max(1,min(k1.width,ALIGNMENT)))
    add_raw("KFZW2","map",k2.address,k2.byte_length,max(1,min(k2.width,ALIGNMENT)))

    # LDRXN/LDRXNZK code points to the 2-byte axis header and expects
    # header + axis + map as a contiguous Bosch structure.
    for name in ("LDRXN","LDRXNZK"):
        m=maps[name]
        if not m.y_axis:
            raise MapSwitchError(f"{name} missing validated axis")
        src=m.y_axis.address-2
        size=2+m.y_axis.byte_length+m.byte_length
        add_raw(name+".BLOCK","header+axis+map",src,size,2)

    kr=maps["KRKTE"]
    add_raw("KRKTE","map",kr.address,kr.byte_length,max(1,min(kr.width,ALIGNMENT)))

    # KFNWEGM can live in the regular copied-bank area because its Bosch
    # lookup uses an explicit R12/R13 (offset,DPP) pair. KFZWMN is special:
    # stock code passes a 16-bit near pointer through the active DPP window,
    # so its added-bank copies are allocated separately in the SAME 16 KiB
    # DPP window as the stock map.
    for runtime_name in ("KFNWEGM","KFTVSA","KFTVSAKAT"):
        if maps.get(runtime_name) is not None:
            m=maps[runtime_name]
            add_raw(runtime_name,"map-runtime",m.address,m.byte_length,max(1,min(m.width,ALIGNMENT)))
    return out

def payload_size(comps):
    c=0
    for _,_,_,size,alignment in comps:
        c=align_up(c,alignment)
        c+=size
    return align_up(c)

def allocate_and_copy(data: bytearray, maps, banks:int, dry_run=False):
    """Plan both regular and near copies before writing; protect source data."""
    if not MIN_BANKS<=banks<=MAX_BANKS:
        raise MapSwitchError('Banks must be 2..4')
    comps=selector_components(maps)
    one=payload_size(comps);total=one*(banks-1)
    if not 0<total<=0x4000:
        raise MapSwitchError('Copied far-map banks must fit one 16 KiB data page')
    protected=[]
    for key,kind,source,size,alignment in comps:
        if not 0<=source<source+size<=len(data):
            raise MapSwitchError(f'{key}: source outside BIN')
        protected.append((source,source+size))
    for name in REQUIRED_MAPS+EXPERIMENTAL_COPY_MAPS:
        m=maps.get(name)
        if m is None:
            continue
        for obj in (m,m.x_axis,m.y_axis):
            if obj is not None:
                if not 0<=obj.address<obj.address+obj.byte_length<=len(data):
                    raise MapSwitchError(f'{name}: invalid source map or axis')
                protected.append((max(0,obj.address-2),obj.address+obj.byte_length))
    hole=None
    for start in (PREFERRED_CAL_START,FALLBACK_CAL_START):
        hole=_find_unreserved_ff(data,total,start,len(data),0x4000,protected)
        if hole is not None:
            break
    if hole is None:
        raise MapSwitchError(f'No contiguous FF area for {banks-1} selector-compatible copies ({total} bytes)')
    layouts=[]
    for bank in range(2,banks+1):
        cursor=hole+(bank-2)*one
        bl=BankLayout(bank=bank,start=cursor,end=cursor+one-1)
        for key,kind,source,size,alignment in comps:
            cursor=align_up(cursor,alignment)
            bl.components.append(BankComponent(key,kind,source,size,cursor))
            cursor+=size
        layouts.append(bl)
    kf=maps.get('KFZWMN')
    if kf is not None:
        page=kf.address&~0x3FFF
        stride=align_up(kf.byte_length,0x10);need=stride*(banks-1)
        local=_find_unreserved_ff(data,need,page,min(len(data),page+0x4000),0x10,
                                  protected+[(hole,hole+total)])
        if local is None:
            raise MapSwitchError(f'KFZWMN: no {need}-byte unreserved FF area in stock DPP window 0x{page:06X}-0x{page+0x3FFF:06X}')
        for index,bl in enumerate(layouts):
            bl.components.append(BankComponent('KFZWMN','map-runtime-near',kf.address,kf.byte_length,local+index*stride))
    if not dry_run:
        source=bytes(data)
        for bl in layouts:
            for c in bl.components:
                if any(x!=0xFF for x in data[c.dest:c.dest+c.size]):
                    raise MapSwitchError(f'{c.key}: allocated range changed before copying')
                data[c.dest:c.dest+c.size]=source[c.source:c.source+c.size]
    return layouts,one,total

def output_name(p:Path,banks:int):
    q=p.with_name(f"{p.stem}_MULTIMAP_{banks}BANK{p.suffix or '.bin'}"); n=2
    while q.exists():
        q=p.with_name(f"{p.stem}_MULTIMAP_{banks}BANK_{n}{p.suffix or '.bin'}"); n+=1
    return q

def choose_banks(arg):
    if arg is not None:
        if not MIN_BANKS <= arg <= MAX_BANKS:
            raise MapSwitchError("Banks must be 2..4")
        return arg

    menu_box("NUMBER OF MAP BANKS", [
        "",
        "  [2]  Bank 1 + Bank 2",
        "  [3]  Bank 1 + Bank 2 + Bank 3",
        "  [4]  Bank 1 + Bank 2 + Bank 3 + Bank 4",
        "",
        "  Bank 1 always remains the original calibration.",
        "  Default selection: 3 banks",
        ""
    ])

    while True:
        raw=prompt_choice("Select number of banks  [2-4]", "3")
        if not raw:
            return 3
        try:
            value=int(raw)
        except ValueError:
            warn("Invalid value. Enter 2, 3 or 4.")
            continue
        if MIN_BANKS <= value <= MAX_BANKS:
            return value
        warn("Invalid value. Enter 2, 3 or 4.")

# ============================================================
# RUNTIME PRE-FLIGHT (NO C167 WRITES YET)
# ============================================================

@dataclass
class RamCandidate:
    address: int
    direct_refs: int
    nearest_symbol_distance: int
    guard_before: int
    guard_after: int
    verdict: str


@dataclass
class RuntimePreflight:
    hook: Optional[int]
    original_hook_bytes: Optional[bytes]
    continuation: Optional[int]
    code_cave: Optional[int]
    code_cave_size: int
    ram_base: Optional[int]
    ram_source: str
    ram_size: int
    ram_candidate: Optional[RamCandidate]
    ready_for_codegen: bool
    notes: list[str]

RUNTIME_HOOK_SIGNATURE = b"\xD7\x40\x06\x02\x03\xF8"
RUNTIME_RAM_SIZE = 0x20
RUNTIME_CODE_START = 0x70000
RAM_SCAN_START = 0x384000
RAM_SCAN_END = 0x385000
RAM_GUARD = 0x20

# C167 four-byte instructions commonly used for direct memory operands in ME7.
# Operand/address is carried in bytes 2..3 little-endian.  The scan is
# deliberately conservative: candidates referenced by any of these families
# are rejected, even if a hit could theoretically be data rather than code.
DIRECT_MEM_OPCODES = {
    0xB2, 0xB6, 0xC2, 0xC6, 0xD2, 0xD6,
    0xE2, 0xE6, 0xF2, 0xF6,
}


def find_runtime_hook(data: bytes):
    # Full overwritten instruction + following page-qualified ADDB sequence.
    rx = re.compile(
        rb"\xF3\xF8..\xD7\x40\x06\x02\x03\xF8.."
        rb"\x9D\x02\xE7\xF8\xFF\x00\xF7\xF8.."
        rb"\xE6\xFC..\xE6\xFD..\xC0\x8E\xC2\xFF..\xDA...\xF7\xF8..\xC2\xF4\x9A\xF8", re.S)
    hits = [m.start() for m in rx.finditer(data) if not m.start() & 1 and m.start() >= 0x20000]
    if len(hits) != 1:
        return None
    return hits[0]


def original_hook_bytes(data: bytes, hook: Optional[int]) -> Optional[bytes]:
    if hook is None or hook < 0 or hook+4 > len(data):
        return None
    return bytes(data[hook:hook+4])


def ranges_overlap(a0:int,a1:int,b0:int,b1:int)->bool:
    return a0 < b1 and b0 < a1


def find_runtime_code_cave(data: bytes, size: int, reserved):
    if size <= 0:
        raise MapSwitchError("Runtime code size must be positive")
    pos = RUNTIME_CODE_START
    while pos + size <= len(data):
        idx = data.find(b"\xFF" * size, pos)
        if idx < 0:
            return None
        aligned = align_up(idx, 0x10)
        if aligned != idx:
            pos = aligned
            continue
        if (idx & 0xFFFF) + size > 0x10000:
            pos = align_up(idx + 1, 0x10000)
            continue
        if all(not ranges_overlap(idx, idx + size, start, end) for start, end in reserved):
            return idx
        pos = idx + 0x10
    return None


def all_ecu_ram_addresses(entries: dict[str,EcuEntry]) -> list[int]:
    out=[]
    for e in entries.values():
        if len(e.columns) < 2: continue
        try: a=int(e.columns[1].strip(),16)
        except Exception: continue
        if 0x380000 <= a <= 0x38FFFF:
            out.append(a)
    return sorted(set(out))


def direct_c167_refs_to_ram_range(data: bytes, ram_start: int, size: int):
    """Conservative direct/pointer reference check, not an indirect-use proof.

    Check unqualified suffix references and EXTP #E1-qualified 14-bit
    operands. Cache the scan once for the input image instead of rescanning
    the entire flash separately for each candidate. Byte loads/stores and
    arithmetic memory operands are included.
    """
    global _ram_reference_cache
    cached = globals().get('_ram_reference_cache')
    if cached is None or cached[0] is not data:
        index = {}
        direct = {0x02,0x03,0x12,0x13,0x22,0x23,0x32,0x33,
                  0x42,0x43,0x52,0x53,0x62,0x63,0x72,0x73,
                  0x82,0x92,0xA2,0xB2,0xC2,0xD2,0xF2,0xF3,
                  0x84,0x94,0xA4,0xB4,0xC5,0xD5,0xF6,0xF7}
        last_e1 = -100
        for pos in range(0, len(data)-3, 2):
            op = data[pos]
            word = int.from_bytes(data[pos+2:pos+4], 'little')
            if op == 0xD7 and data[pos+1] & 0xC0 == 0x40 and word == 0xE1:
                last_e1 = pos
            if op in direct or op == 0xE6:
                targets = set()
                if 0x4000 <= word < 0x5000:
                    targets.add(0x380000 + word)
                if pos-last_e1 <= 20 and (word & 0x3FFF) < 0x1000:
                    targets.add(0x384000 + (word & 0x3FFF))
                # Immediate far-pointer pair, e.g. MOV R12,#off; MOV R13,#E1.
                if op == 0xE6 and data[pos+4:pos+6] == bytes((0xE6, (data[pos+1]+1)&255)) and data[pos+6:pos+8] == b'\xE1\x00':
                    targets.add(0x384000 + (word & 0x3FFF))
                for target in targets:
                    index.setdefault(target, []).append(pos)
        _ram_reference_cache = (data, index)
    return sorted({p for a, refs in _ram_reference_cache[1].items()
                   if ram_start-1 <= a < ram_start+size for p in refs})


def ram_gap_candidates(entries, size=RUNTIME_RAM_SIZE):
    occupied = []
    for start, end in _ecu_ram_spans(entries):
        start = max(RAM_SCAN_START, start - RAM_GUARD)
        end = min(RAM_SCAN_END, end + RAM_GUARD)
        if start >= end:
            continue
        if occupied and start <= occupied[-1][1]:
            occupied[-1] = (occupied[-1][0], max(occupied[-1][1], end))
        else:
            occupied.append((start, end))
    gaps = []
    prev = RAM_SCAN_START + RAM_GUARD
    for start, end in occupied + [(RAM_SCAN_END - RAM_GUARD, RAM_SCAN_END)]:
        for addr in range(align_up(prev), start - size + 1, 0x10):
            before, after = addr - prev + RAM_GUARD, start - addr - size + RAM_GUARD
            gaps.append((addr, min(before, after), before, after))
        prev = max(prev, end)
    return gaps


def find_strong_ram_candidate(data: bytes, entries: dict[str,EcuEntry], size:int=RUNTIME_RAM_SIZE) -> Optional[RamCandidate]:
    ranked=[]
    for addr,nearest,before,after in ram_gap_candidates(entries,size):
        refs=direct_c167_refs_to_ram_range(data,addr,size)
        if refs:
            continue
        ranked.append(RamCandidate(addr,0,nearest,before,after,"STRONG CANDIDATE"))
    if not ranked:
        return None
    # Prefer the candidate with the largest separation from named symbols;
    # ties prefer the higher XRAM address, matching our previous allocator.
    ranked.sort(key=lambda c:(c.nearest_symbol_distance,c.address),reverse=True)
    return ranked[0]


def analyze_explicit_ram(data, entries, addr, size):
    refs = direct_c167_refs_to_ram_range(data, addr, size)
    spans = _ecu_ram_spans(entries)
    overlaps = any(ranges_overlap(addr, addr+size, start, end) for start,end in spans)
    nearest = min((min(abs(start-addr),abs(end-addr)) for start,end in spans), default=0xFFFF)
    valid = not addr & 1 and RAM_SCAN_START <= addr and addr+size <= RAM_SCAN_END
    verdict = 'PASS' if valid and not refs and not overlaps else 'REVIEW'
    return RamCandidate(addr,len(refs),nearest,0,0,verdict)


def runtime_preflight(data: bytes, entries: dict[str,EcuEntry], layouts, code_size:int, explicit_ram:Optional[int], accept_auto_ram:bool=False) -> RuntimePreflight:
    notes=[]
    hook=find_runtime_hook(data)
    orig=original_hook_bytes(data,hook)
    continuation=(hook+4) if hook is not None else None
    reserved=[(bl.start,bl.end+1) for bl in layouts]
    reserved.extend((c.dest,c.dest+c.size) for bl in layouts for c in bl.components)
    cave=find_runtime_code_cave(data,code_size,reserved)

    candidate=None
    if explicit_ram is not None:
        ram=explicit_ram
        candidate=analyze_explicit_ram(data,entries,ram,RUNTIME_RAM_SIZE)
        ram_source=f"explicit --ram-base ({candidate.verdict})"
        if not (RAM_SCAN_START <= ram and ram+RUNTIME_RAM_SIZE <= RAM_SCAN_END):
            notes.append("Explicit RAM base is outside the tested 0x384000-0x384FFF direct-addressing window.")
        if candidate.direct_refs:
            notes.append(f"Explicit RAM range has {candidate.direct_refs} direct C167 reference(s) in ROM; review required.")
    else:
        candidate=find_strong_ram_candidate(data,entries,RUNTIME_RAM_SIZE)
        ram=candidate.address if candidate else None
        ram_source="auto RAM safety scan (STRONG CANDIDATE)" if candidate else "not found"
        if candidate and not accept_auto_ram:
            notes.append("Auto RAM candidate has no named .ecu symbols and no detected direct C167 references, but indirect accesses cannot be excluded.")
            notes.append("Use --accept-auto-ram for BENCH code-generation pre-flight only after reviewing this candidate.")

    if hook is None: notes.append("Cyclic runtime hook signature not found.")
    elif orig is None: notes.append("Cyclic hook found but original 4-byte instruction could not be read.")
    if cave is None: notes.append(f"No FF code cave >= 0x{code_size:X} bytes found above 0x{RUNTIME_CODE_START:X}.")
    if ram is None: notes.append("No RAM candidate passed symbol-gap + direct-reference safety scan.")

    ram_accepted = explicit_ram is not None or (candidate is not None and accept_auto_ram)
    candidate_ok = candidate is not None and candidate.direct_refs == 0 and candidate.verdict in {"PASS","STRONG CANDIDATE"}
    ready = hook is not None and orig is not None and cave is not None and ram is not None and ram_accepted and candidate_ok
    return RuntimePreflight(hook,orig,continuation,cave,code_size,ram,ram_source,RUNTIME_RAM_SIZE,candidate,ready,notes)


def print_runtime_preflight(r:RuntimePreflight, mode:str):
    print("\n"+"─"*72)
    print(" RUNTIME PRE-FLIGHT")
    print("─"*72)
    print(f"  Cyclic hook       : {('0x%06X'%r.hook) if r.hook is not None else 'NOT FOUND'}")
    print(f"  Original hook     : {r.original_hook_bytes.hex(' ').upper() if r.original_hook_bytes is not None else 'NOT FOUND'}")
    print(f"  Continue at       : {('0x%06X'%r.continuation) if r.continuation is not None else 'NOT FOUND'}")
    print(f"  Runtime code cave : {('0x%06X'%r.code_cave) if r.code_cave is not None else 'NOT FOUND'}  ({r.code_cave_size} B reserved)")
    print(f"  Runtime RAM       : {('0x%06X'%r.ram_base) if r.ram_base is not None else 'NOT FOUND'}  ({r.ram_source})")
    print(f"  Runtime RAM size  : 0x{r.ram_size:X} bytes")
    if r.ram_candidate is not None:
        c=r.ram_candidate
        print(f"  Direct ROM refs   : {c.direct_refs}")
        print(f"  Nearest .ecu sym  : 0x{c.nearest_symbol_distance:X} bytes")
        print(f"  RAM verdict       : {c.verdict}")
    print(f"  Trigger mode      : {'Clutch + SET/RES' if mode=='cruise' else 'Clutch + 5x Brake (no time limit)'}")
    print("\n  Planned RAM layout")
    if r.ram_base is not None:
        if mode=='cruise':
            fields=[('current_bank',0,2),('prev_set',2,2),('prev_res',4,2),('tach_ticks',6,2),('reserved_1',8,2),('reserved_2',10,2),('runtime_magic',12,2)]
        else:
            fields=[('current_bank',0,2),('prev_brake',2,2),('brake_count',4,2),('tach_ticks',6,2),('reserved_1',8,2),('reserved_2',10,2),('runtime_magic',12,2)]
        for name,off,sz in fields:
            print(f"    {name:<18} 0x{r.ram_base+off:06X}  ({sz} B)")
    else:
        print("    unavailable")
    print("\n  Status")
    if r.ready_for_codegen:
        print("    PRE-FLIGHT PASS — hook, cave and RAM candidate accepted.")
    else:
        print("    PRE-FLIGHT INCOMPLETE — runtime patching remains blocked.")
    for note in r.notes:
        print(f"    - {note}")


# ============================================================
# BENCH RUNTIME C167 — BANK COUNTER + TACH TIMER, NO MAP SELECTORS
# ============================================================
# This stage deliberately does NOT redirect any calibration lookup.  It only
# proves the cyclic hook, input edge logic, current_bank state and tach timer.
# Use on a bench ECU first. Tach duration is expressed in raw scheduler calls.

R4 = 0xF4
CC_UC  = 0x0
CC_Z   = 0x2
CC_NZ  = 0x3
CC_ULE = 0xF


def _le16(v:int)->bytes:
    return bytes((v & 0xFF, (v >> 8) & 0xFF))


def _ram_word(addr: int) -> int:
    if not (RAM_SCAN_START <= addr < RAM_SCAN_END) or addr & 1:
        raise MapSwitchError(f"Unaligned/out-of-range runtime WORD: 0x{addr:06X}")
    return addr & 0x3FFF


def _bitoff(address: int) -> int:
    if address & 1:
        raise MapSwitchError(f"Unaligned bit-addressable WORD: 0x{address:06X}")
    if 0xFD00 <= address <= 0xFDFE:
        return (address - 0xFD00) // 2
    if 0xFF00 <= address <= 0xFFDE:
        return 0x80 + (address - 0xFF00) // 2
    raise MapSwitchError(f"Unsupported bit-addressable WORD: 0x{address:06X}")


def _calls_operand(code_cave: int) -> bytes:
    if code_cave & 1 or not 0 <= code_cave < EXPECTED_SIZE:
        raise MapSwitchError(f"Invalid CALLS destination 0x{code_cave:X}")
    addr = code_cave + 0x800000
    return bytes((addr >> 16, addr & 255, (addr >> 8) & 255))


class _C167Builder:
    def __init__(self):
        self.b=bytearray()
        self.labels={}
        self.fix=[]
    def label(self,name):
        if name in self.labels: raise MapSwitchError(f"Duplicate runtime label: {name}")
        self.labels[name]=len(self.b)
    def emit(self,*parts):
        for p in parts:
            if isinstance(p,int): self.b.append(p & 0xFF)
            else: self.b.extend(p)
    def mov_r4_mem(self,addr): _emit_mov_reg_mem(self,R4,addr)
    def mov_mem_r4(self,addr):
        operand=_ram_word(addr)
        _emit_extp(self,addr >> 14)
        self.emit(0xF6,R4,_le16(operand))
    def mov_r4_imm(self,val): self.emit(0xE6,R4,_le16(val))
    def cmp_r4_imm(self,val): self.emit(0x46,R4,_le16(val))
    def add_r4_1(self): self.emit(0x08,0x41)  # ADD R4,#1
    def sub_r4_1(self): self.emit(0x28,0x41)  # SUB R4,#1
    def jmpr(self,cc,label):
        pos=len(self.b); self.emit((cc<<4)|0x0D,0x00); self.fix.append(('rel2',pos,label))
    def jbit(self,is_set,address,bit,label):
        if not 0 <= bit <= 15: raise MapSwitchError(f"Invalid bit number {bit}")
        pos=len(self.b)
        self.emit(0x8A if is_set else 0x9A,_bitoff(address),0x00,(bit<<4)&0xF0)
        self.fix.append(('rel4',pos,label))
    def bset(self,address,bit): self.emit(((bit&0xF)<<4)|0x0F,_bitoff(address))
    def bclr(self,address,bit): self.emit(((bit&0xF)<<4)|0x0E,_bitoff(address))
    def finish(self):
        for typ,pos,label in self.fix:
            if label not in self.labels: raise MapSwitchError(f"Missing runtime label: {label}")
            ilen=2 if typ=='rel2' else 4
            delta=self.labels[label]-(pos+ilen)
            if delta % 2: raise MapSwitchError(f"Unaligned C167 branch to {label}")
            words=delta//2
            if not -128 <= words <= 127:
                raise MapSwitchError(f"C167 relative branch to {label} out of range ({words} words)")
            idx=pos+1 if typ=='rel2' else pos+2
            self.b[idx]=words & 0xFF
        return bytes(self.b)


def _tick_tach_timer(a:_C167Builder, timer_addr:int, done_label:str):
    a.mov_r4_mem(timer_addr)
    a.cmp_r4_imm(0)
    a.jmpr(CC_Z,done_label)
    a.sub_r4_1()
    a.mov_mem_r4(timer_addr)
    a.label(done_label)


def build_bench_brake5_runtime(*,banks:int,ram_base:int,clutch:SwitchInput,brake:SwitchInput,
                               orig_hook:bytes,tach_ticks:int)->bytes:
    if not 2 <= banks <= 4: raise MapSwitchError("Bench runtime banks must be 2..4")
    if len(orig_hook)!=4 or orig_hook[:2] != b'\xF3\xF8':
        raise MapSwitchError(f"Bench runtime currently requires original hook F3 F8 xx xx, got {orig_hook.hex(' ').upper()}")
    if tach_ticks < 1 or tach_ticks > 0x7FFF:
        raise MapSwitchError("--tach-ticks must be 1..32767 scheduler calls")

    BANK=ram_base+0
    PREV=ram_base+2
    COUNT=ram_base+4
    TACH=ram_base+6
    RES1=ram_base+8
    RES2=ram_base+10
    MAGIC=ram_base+12
    a=_C167Builder()
    a.emit(0xEC,0x88,0xEC,R4)

    a.mov_r4_mem(MAGIC); a.cmp_r4_imm(0x4D53); a.jmpr(CC_Z,'initialized')
    a.mov_r4_imm(1); a.mov_mem_r4(BANK)
    a.mov_r4_imm(0)
    for addr in (PREV,COUNT,TACH,RES1,RES2): a.mov_mem_r4(addr)
    a.mov_r4_imm(0x4D53); a.mov_mem_r4(MAGIC)
    a.label('initialized')

    a.mov_r4_mem(BANK); a.cmp_r4_imm(0); a.jmpr(CC_NZ,'bank_nonzero')
    a.mov_r4_imm(1); a.mov_mem_r4(BANK)
    a.label('bank_nonzero')
    a.mov_r4_mem(BANK); a.cmp_r4_imm(banks); a.jmpr(CC_ULE,'bank_valid')
    a.mov_r4_imm(1); a.mov_mem_r4(BANK)
    a.label('bank_valid')

    _tick_tach_timer(a,TACH,'trigger')

    a.jbit(False,clutch.address,clutch.bit,'clutch_off')
    a.jbit(False,brake.address,brake.bit,'brake_off')
    a.mov_r4_mem(PREV); a.cmp_r4_imm(0); a.jmpr(CC_NZ,'exit')
    a.mov_r4_imm(1); a.mov_mem_r4(PREV)
    a.mov_r4_mem(COUNT); a.add_r4_1(); a.mov_mem_r4(COUNT)
    a.cmp_r4_imm(5); a.jmpr(CC_NZ,'exit')

    a.mov_r4_imm(0); a.mov_mem_r4(COUNT)
    a.mov_r4_mem(BANK); a.add_r4_1(); a.cmp_r4_imm(banks); a.jmpr(CC_ULE,'store_bank')
    a.mov_r4_imm(1)
    a.label('store_bank')
    a.mov_mem_r4(BANK)
    a.mov_r4_imm(tach_ticks); a.mov_mem_r4(TACH)
    a.jmpr(CC_UC,'exit')

    a.label('brake_off')
    a.mov_r4_imm(0); a.mov_mem_r4(PREV); a.jmpr(CC_UC,'exit')

    a.label('clutch_off')
    a.mov_r4_imm(0); a.mov_mem_r4(COUNT)
    a.jbit(False,brake.address,brake.bit,'clutch_brake_clear')
    a.mov_r4_imm(1); a.mov_mem_r4(PREV); a.jmpr(CC_UC,'exit')
    a.label('clutch_brake_clear')
    a.mov_r4_imm(0); a.mov_mem_r4(PREV)

    a.label('exit')
    a.emit(0xFC,R4,0xFC,0x88)
    a.emit(orig_hook,0xDB,0x00)
    code=a.finish()
    while len(code)%16: code += b'\xFF'
    return code


def build_bench_cruise_runtime(*,banks:int,ram_base:int,clutch:SwitchInput,
                               cruise_set:SwitchInput,cruise_res:SwitchInput,
                               orig_hook:bytes,tach_ticks:int)->bytes:
    if not 2 <= banks <= 4:
        raise MapSwitchError("Bench runtime banks must be 2..4")
    if len(orig_hook)!=4 or orig_hook[:2] != b'\xF3\xF8':
        raise MapSwitchError(f"Bench runtime currently requires original hook F3 F8 xx xx, got {orig_hook.hex(' ').upper()}")
    if tach_ticks < 1 or tach_ticks > 0x7FFF:
        raise MapSwitchError("--tach-ticks must be 1..32767 scheduler calls")

    BANK=ram_base+0
    PREV_SET=ram_base+2
    PREV_RES=ram_base+4
    TACH=ram_base+6
    RES1=ram_base+8
    RES2=ram_base+10
    MAGIC=ram_base+12
    a=_C167Builder()
    a.emit(0xEC,0x88,0xEC,R4)

    a.mov_r4_mem(MAGIC); a.cmp_r4_imm(0x4D53); a.jmpr(CC_Z,'initialized')
    a.mov_r4_imm(1); a.mov_mem_r4(BANK)
    a.mov_r4_imm(0)
    for addr in (PREV_SET,PREV_RES,TACH,RES1,RES2): a.mov_mem_r4(addr)
    a.mov_r4_imm(0x4D53); a.mov_mem_r4(MAGIC)
    a.label('initialized')

    a.mov_r4_mem(BANK); a.cmp_r4_imm(0); a.jmpr(CC_NZ,'bank_nonzero')
    a.mov_r4_imm(1); a.mov_mem_r4(BANK)
    a.label('bank_nonzero')
    a.mov_r4_mem(BANK); a.cmp_r4_imm(banks); a.jmpr(CC_ULE,'bank_valid')
    a.mov_r4_imm(1); a.mov_mem_r4(BANK)
    a.label('bank_valid')

    _tick_tach_timer(a,TACH,'trigger')

    a.jbit(False,clutch.address,clutch.bit,'clutch_off')
    a.jbit(False,cruise_set.address,cruise_set.bit,'buttons_separate')
    a.jbit(False,cruise_res.address,cruise_res.bit,'buttons_separate')
    a.mov_r4_imm(1); a.mov_mem_r4(PREV_SET); a.mov_mem_r4(PREV_RES)
    a.jmpr(CC_UC,'cruise_early_exit')
    a.label('buttons_separate')

    a.jbit(False,cruise_set.address,cruise_set.bit,'set_clear')
    a.mov_r4_mem(PREV_SET); a.cmp_r4_imm(0); a.jmpr(CC_NZ,'check_res')
    a.mov_r4_imm(1); a.mov_mem_r4(PREV_SET)
    a.mov_r4_mem(BANK); a.add_r4_1(); a.cmp_r4_imm(banks); a.jmpr(CC_ULE,'store_next')
    a.mov_r4_imm(1)
    a.label('store_next')
    a.mov_mem_r4(BANK)
    a.mov_r4_imm(tach_ticks); a.mov_mem_r4(TACH)
    a.label('cruise_early_exit')
    a.jmpr(CC_UC,'exit')

    a.label('set_clear')
    a.mov_r4_imm(0); a.mov_mem_r4(PREV_SET)

    a.label('check_res')
    a.jbit(False,cruise_res.address,cruise_res.bit,'res_clear')
    a.mov_r4_mem(PREV_RES); a.cmp_r4_imm(0); a.jmpr(CC_NZ,'exit')
    a.mov_r4_imm(1); a.mov_mem_r4(PREV_RES)
    a.mov_r4_mem(BANK); a.cmp_r4_imm(1); a.jmpr(CC_Z,'wrap_prev')
    a.sub_r4_1(); a.jmpr(CC_UC,'store_prev')
    a.label('wrap_prev')
    a.mov_r4_imm(banks)
    a.label('store_prev')
    a.mov_mem_r4(BANK)
    a.mov_r4_imm(tach_ticks); a.mov_mem_r4(TACH)
    a.jmpr(CC_UC,'exit')

    a.label('res_clear')
    a.mov_r4_imm(0); a.mov_mem_r4(PREV_RES); a.jmpr(CC_UC,'exit')

    a.label('clutch_off')
    a.jbit(False,cruise_set.address,cruise_set.bit,'clutch_set_clear')
    a.mov_r4_imm(1); a.mov_mem_r4(PREV_SET); a.jmpr(CC_UC,'sync_res')
    a.label('clutch_set_clear')
    a.mov_r4_imm(0); a.mov_mem_r4(PREV_SET)
    a.label('sync_res')
    a.jbit(False,cruise_res.address,cruise_res.bit,'clutch_res_clear')
    a.mov_r4_imm(1); a.mov_mem_r4(PREV_RES); a.jmpr(CC_UC,'exit')
    a.label('clutch_res_clear')
    a.mov_r4_imm(0); a.mov_mem_r4(PREV_RES)

    a.label('exit')
    a.emit(0xFC,R4,0xFC,0x88)
    a.emit(orig_hook,0xDB,0x00)
    code=a.finish()
    while len(code)%16: code += b'\xFF'
    return code


@dataclass(frozen=True)
class TachHook:
    address:int
    original:bytes
    source_addr:int
    factor:float


def _find_mifab_can_anchor(binary:bytes, entries:dict[str,EcuEntry]):
    """Find the CAN byte occupied by mifab_w, following the NefMoto method.

    fknbrkn's method is to use a rarely referenced variable from the same
    Motor1/0x280 frame as an anchor.  For ME7.5 that variable is commonly
    mifab_w.  Its CAN-byte store identifies the local F8xx CAN buffer; nmot_w
    occupies the two immediately preceding bytes in the same frame.

    Returns (mifab_store_instruction, mifab_can_byte, nmot_hi_byte, nmot_lo_byte)
    or None when a unique anchor cannot be proven.
    """
    mifab=ecu_address(entries,"mifab_w")
    if mifab is None:
        return None

    # mifab_w in supplied ME7Info files is commonly XRAM.  Look for a page-
    # qualified WORD load into any R0..R15:
    #   EXTP #(addr>>14),#1
    #   MOV  Rn,[addr&0x3fff]
    page=mifab >> 14
    op=mifab & 0x3fff
    extp=b'\xD7\x40'+_le16(page)
    loads=[]
    for pos in range(0,len(binary)-8,2):
        if binary[pos:pos+4] != extp:
            continue
        # permit a very small amount of compiler scheduling after EXTP
        lim=min(len(binary)-4,pos+16)
        q=pos+4
        while q<=lim:
            ins=binary[q:q+4]
            if len(ins)==4 and ins[0]==0xF2 and ins[2:4]==_le16(op):
                loads.append((pos,q,ins[1]))
            q+=2

    anchors=[]
    # After a mifab load, locate a direct MOVB into F8xx.  F7 xx aa F8 is the
    # direct-byte-store family used by the stock CAN packer.  We intentionally
    # do not hardcode the source register; compiler variants may use R4/R5/etc.
    for _,load_pos,_reg in loads:
        end=min(len(binary)-4,load_pos+0x80)
        q=load_pos+4
        while q<=end:
            ins=binary[q:q+4]
            if ins[0]==0xF7 and ins[3]==0xF8:
                dst=0xF800 | ins[2]
                # Typical CAN staging bytes live in the lower part of F8xx.
                if 0xF800 <= dst <= 0xF8FF:
                    # Forum example: mifab at byte 5 (F826), nmot word at
                    # bytes 3/4 (F824/F825).  Therefore derive, don't assume.
                    if (dst & 0xff) >= 2:
                        anchors.append((q,dst,dst-2,dst-1))
                break
            q+=2

    # Collapse duplicate observations of the same CAN destination.
    unique={a[1]:a for a in anchors}
    if len(unique)==1:
        return next(iter(unique.values()))
    return None


def find_tach_hook(binary:bytes, entries:dict[str,EcuEntry]) -> TachHook:
    """Locate the stock ME7.5 nmot_w -> cluster CAN path.

    Detection order follows the NefMoto guidance:
      1) Try the common direct F824/F825 layout.
      2) If that is absent, locate mifab_w in the same 0x280/Motor1 message,
         derive the local F8xx CAN-byte positions, then identify the nmot_w
         load feeding those two stores.

    Only the stock MOV R4,nmot_w instruction is replaced; the stock CAN-byte
    stores remain untouched.
    """
    source,factor=resolve_tach_source(entries)
    load=b'\xF2\xF4'+_le16(source)
    load_hits=[p for p in range(0,len(binary)-3,2) if binary[p:p+4]==load]

    def candidates_for_bytes(byte_a:int, byte_b:int):
        # Match direct MOVB stores to the two derived bytes, regardless of
        # whether low/high appears first and without assuming fixed register
        # nibble encodings.  We still require both stores close to the nmot load.
        out=[]
        WINDOW=0x100
        for p in load_hits:
            end=min(len(binary)-4,p+4+WINDOW)
            hits={byte_a:[],byte_b:[]}
            q=p+4
            while q<=end:
                ins=binary[q:q+4]
                if ins[0]==0xF7 and ins[3]==0xF8:
                    dst=0xF800 | ins[2]
                    if dst in hits:
                        hits[dst].append(q)
                q+=2
            if not hits[byte_a] or not hits[byte_b]:
                continue
            # Pick the closest pair and require them to be in one compact packer.
            pair=min(((a,b) for a in hits[byte_a] for b in hits[byte_b]),
                     key=lambda x:abs(x[0]-x[1]))
            if abs(pair[0]-pair[1]) <= 0x30:
                out.append((p,pair[0],pair[1],byte_a,byte_b))
        return out

    # Common layout from the forum first.
    candidates=candidates_for_bytes(0xF824,0xF825)
    anchor=None
    if len(candidates)!=1:
        anchor=_find_mifab_can_anchor(binary,entries)
        if anchor is not None:
            _mpos,mifab_byte,nmot_b0,nmot_b1=anchor
            candidates=candidates_for_bytes(nmot_b0,nmot_b1)

    if len(candidates)!=1:
        detail=(f"nmot_w loads={len(load_hits)}, validated CAN candidates={len(candidates)}")
        if load_hits:
            detail += ", load addresses=" + ",".join(f"0x{x:06X}" for x in load_hits[:12])
        if anchor is None:
            mifab=ecu_address(entries,"mifab_w")
            detail += f", mifab_w={'not found' if mifab is None else f'0x{mifab:06X} but CAN anchor not unique'}"
        else:
            _mpos,mifab_byte,n0,n1=anchor
            detail += f", mifab CAN=0x{mifab_byte:04X}, derived nmot bytes=0x{n0:04X}/0x{n1:04X}"
        if candidates:
            detail += ", candidates=" + ",".join(
                f"0x{p:06X}[0x{b0:04X}@0x{s0:06X},0x{b1:04X}@0x{s1:06X}]"
                for p,s0,s1,b0,b1 in candidates[:8])
        raise MapSwitchError("Tach CAN hook: expected exactly 1 validated nmot_w CAN path; "+detail)

    p,_,_,_,_=candidates[0]
    return TachHook(p,bytes(binary[p:p+4]),source,factor)

def build_tach_wrapper(*,banks:int,ram_base:int,stock_mov:bytes,raw_values:list[int])->bytes:
    if len(stock_mov)!=4 or stock_mov[:2] != b'\xF2\xF4':
        raise MapSwitchError("Tach wrapper requires stock MOV R4,nmot_w")
    if len(raw_values)!=banks:
        raise MapSwitchError("Tach raw-value count does not match bank count")
    BANK=ram_base+0
    TACH=ram_base+6
    MAGIC=ram_base+12
    a=_C167Builder()
    a.emit(0xEC,0x88)  # preserve caller PSW; R4 is intentional return value
    a.mov_r4_mem(MAGIC); a.cmp_r4_imm(0x4D53); a.jmpr(CC_NZ,'stock')
    a.mov_r4_mem(TACH); a.cmp_r4_imm(0); a.jmpr(CC_Z,'stock')
    a.mov_r4_mem(BANK)
    for bank in range(1,banks+1):
        a.cmp_r4_imm(bank); a.jmpr(CC_Z,f'bank_{bank}')
    a.jmpr(CC_UC,'stock')
    for bank,raw in enumerate(raw_values,1):
        a.label(f'bank_{bank}')
        a.mov_r4_imm(raw)
        a.emit(0xFC,0x88)
        a.emit(0xDB,0x00)
    a.label('stock')
    a.emit(stock_mov)
    a.emit(0xFC,0x88)
    a.emit(0xDB,0x00)
    code=a.finish()
    while len(code)%16: code += b'\xFF'
    return code


def install_bench_runtime(data:bytearray,rt:RuntimePreflight,*,banks:int,mode:str,
                          switch_inputs:list[SwitchInput],tach_ticks:int,
                          entries:dict[str,EcuEntry],original:bytes):
    if not rt.ready_for_codegen:
        raise MapSwitchError("Runtime pre-flight did not pass; bench runtime installation blocked")
    byrole={x.role:x for x in switch_inputs}
    if 'Clutch' not in byrole:
        raise MapSwitchError("Clutch input unavailable for bench runtime")
    if rt.hook is None or rt.code_cave is None or rt.ram_base is None or rt.original_hook_bytes is None:
        raise MapSwitchError("Incomplete runtime resources")

    if mode == 'brake5':
        if 'Brake' not in byrole: raise MapSwitchError("Brake input unavailable for brake5 bench runtime")
        runtime=build_bench_brake5_runtime(
            banks=banks,ram_base=rt.ram_base,clutch=byrole['Clutch'],brake=byrole['Brake'],
            orig_hook=rt.original_hook_bytes,tach_ticks=tach_ticks)
    elif mode == 'cruise':
        if 'Cruise SET' not in byrole or 'Cruise RES' not in byrole:
            raise MapSwitchError("Cruise SET/RES inputs unavailable for cruise bench runtime")
        runtime=build_bench_cruise_runtime(
            banks=banks,ram_base=rt.ram_base,clutch=byrole['Clutch'],
            cruise_set=byrole['Cruise SET'],cruise_res=byrole['Cruise RES'],
            orig_hook=rt.original_hook_bytes,tach_ticks=tach_ticks)
    else:
        raise MapSwitchError(f"Unsupported bench runtime mode: {mode}")

    tach_hook=find_tach_hook(original,entries)
    raw=tach_raw_values(entries,banks)
    tach_code=build_tach_wrapper(banks=banks,ram_base=rt.ram_base,
                                 stock_mov=tach_hook.original,raw_values=raw)
    tach_cave=align_up(rt.code_cave+len(runtime),0x10)
    used=(tach_cave-rt.code_cave)+len(tach_code)
    if used>rt.code_cave_size:
        raise MapSwitchError(f"Runtime + tach wrapper need {used} B, reserved cave is {rt.code_cave_size} B")
    if any(x != 0xFF for x in data[rt.code_cave:rt.code_cave+used]):
        raise MapSwitchError("Runtime/tach code cave is no longer FF at write time")
    if bytes(data[tach_hook.address:tach_hook.address+4]) != tach_hook.original:
        raise MapSwitchError("Tach CAN hook bytes changed before write")

    data[rt.code_cave:rt.code_cave+len(runtime)]=runtime
    data[tach_cave:tach_cave+len(tach_code)]=tach_code
    data[rt.hook:rt.hook+4]=b'\xDA'+_calls_operand(rt.code_cave)
    data[tach_hook.address:tach_hook.address+4]=b'\xDA'+_calls_operand(tach_cave)

    if bytes(data[tach_hook.address:tach_hook.address+4]) != b'\xDA'+_calls_operand(tach_cave):
        raise MapSwitchError("Tach hook verification failed")
    return runtime,tach_hook,tach_cave,tach_code,raw

def bench_output_name(p:Path,banks:int):
    q=p.with_name(f"{p.stem}_BENCH_MAPSWITCH_RUNTIME_{banks}BANK{p.suffix or '.bin'}"); n=2
    while q.exists():
        q=p.with_name(f"{p.stem}_BENCH_MAPSWITCH_RUNTIME_{banks}BANK_{n}{p.suffix or '.bin'}"); n+=1
    return q




def _hex_window(binary: bytes, at: int | None, size: int = 48) -> str:
    if at is None or at < 0 or at >= len(binary):
        return "-"
    end=min(len(binary),at+size)
    return " ".join(f"{x:02X}" for x in binary[at:end])

def print_selector_analysis(binary: bytes, maps, sources):
    section("MAP ACCESS VALIDATION")

    rows=[]
    for name in ("LAMFA","KFZW","KFZW2","LDRXN","LDRXNZK","KRKTE"):
        m=maps.get(name)
        if m is None:
            rows.append((name,None,"NOT FOUND"))
            continue

        at=m.pattern_location
        if name in ("KFZW","KFZW2"):
            label="Ignition lookup"
        elif name in ("LDRXN","LDRXNZK"):
            label="Load lookup"
        elif name=="LAMFA":
            label="Lambda lookup"
        else:
            label="Injector constant read"

        rows.append((name,at,label))

    passed=True
    for name,addr,label in rows:
        if addr is None:
            passed=False
            print(f"  {_tag('[FAIL]',_RED)} {name:<9}  access point not found")
        else:
            print(f"  {_tag('[ OK ]',_GREEN)} {name:<9}  code 0x{addr:06X}   {label}")

    print()
    print("  " + "─"*66)
    if passed:
        ok("All required map access anchors located; full call-site checks follow")
    else:
        warn("Map access validation incomplete")

def _flash_operand(addr: int) -> tuple[int,int,int]:
    """Return (segment_base, dpp, 16-bit operand) for a 16KB DPP window."""
    base = addr & ~0x3FFF
    dpp = (base + 0x800000) // 0x4000
    return base, dpp, addr - base

def _find_operand_instructions(binary: bytes, anchor: int, target: int, radius: int = 112):
    """Find 4-byte C167-looking instructions near anchor whose imm16 points at target
    within its 16KB DPP window. This does not patch anything.
    """
    _, dpp, op = _flash_operand(target)
    lo = op & 0xFF
    hi = (op >> 8) & 0xFF
    start=max(0,anchor-radius)
    end=min(len(binary)-4,anchor+radius)
    hits=[]
    # Common absolute/pointer opcodes seen in the supplied Audi XML C167 sites.
    allowed={0xE6,0xF2,0xC2,0xF6}
    for p in range(start,end+1):
        if binary[p] not in allowed:
            continue
        if binary[p+2]==lo and binary[p+3]==hi:
            hits.append((p,bytes(binary[p:p+4]),dpp,op))
    return hits

def print_patchsite_preflight(binary: bytes, maps, layouts):
    print("\n"+"─"*72)
    print(" SELECTOR PATCH-SITE PREFLIGHT")
    print("─"*72)
    checks=[]

    def add(label, map_name, target, anchor):
        hits=_find_operand_instructions(binary,anchor,target) if anchor is not None else []
        checks.append((label,map_name,target,anchor,hits))

    lam=maps["LAMFA"]
    add("LAMFA map", "LAMFA", lam.address, lam.pattern_location)
    if lam.x_axis:
        add("LAMFA X payload", "LAMFA", lam.x_axis.address, lam.pattern_location)
    if lam.y_axis:
        # Bosch lookup points at the axis header/length word when present.
        add("LAMFA Y header", "LAMFA", lam.y_axis.address-2, lam.pattern_location)

    k1=maps["KFZW"]; k2=maps["KFZW2"]
    add("KFZW map", "KFZW", k1.address, k1.pattern_location)
    add("KFZW2 map", "KFZW2", k2.address, k2.pattern_location)

    l1=maps["LDRXN"]; l2=maps["LDRXNZK"]
    if l1.y_axis:
        add("LDRXN axis header", "LDRXN", l1.y_axis.address-2, l1.pattern_location)
    if l2.y_axis:
        add("LDRXNZK axis header", "LDRXNZK", l2.y_axis.address-2, l2.pattern_location)

    kr=maps["KRKTE"]
    add("KRKTE scalar", "KRKTE", kr.address, kr.pattern_location)

    unique=True
    for label,name,target,anchor,hits in checks:
        base,dpp,op=_flash_operand(target)
        if len(hits)==1:
            p,b,_,_=hits[0]
            print(f"[ OK ] {label:<20} target=0x{target:06X} instr=0x{p:06X}  bytes={' '.join(f'{x:02X}' for x in b)}")
            print(f"       DPP=0x{dpp:03X} base=0x{base:06X} operand=0x{op:04X}")
        elif len(hits)==0:
            unique=False
            print(f"[FAIL] {label:<20} target=0x{target:06X}  no matching 4-byte operand instruction near 0x{anchor:06X}")
        else:
            unique=False
            hs=", ".join(f"0x{p:06X}" for p,_,_,_ in hits)
            print(f"[AMB ] {label:<20} target=0x{target:06X}  {len(hits)} matches: {hs}")

    print("\n  Bank-copy compatibility")
    # Existing v0.13 allocator copies axis payloads but not the 2-byte Bosch
    # length/header immediately preceding LDRXN/LDRXNZK axes.  Flag this now,
    # before any selector is allowed to use the copied blocks.
    required_headers=[]
    if l1.y_axis: required_headers.append(("LDRXN", l1.y_axis.address-2, 2))
    if l2.y_axis: required_headers.append(("LDRXNZK", l2.y_axis.address-2, 2))
    if lam.y_axis: required_headers.append(("LAMFA.Y", lam.y_axis.address-2, 2))
    copied_sources=set()
    for bl in layouts:
        for c in bl.components:
            copied_sources.add((c.source,c.size))
    header_ok=True
    for name,src,sz in required_headers:
        present=(src,sz) in copied_sources
        print(f"    {name:<10} header 0x{src:06X} ({sz} B): {'COPIED' if present else 'NOT COPIED'}")
        if not present: header_ok=False

    print("\n  PREFLIGHT VERDICT")
    if unique and header_ok:
        print("    PASS — exact operand instructions are unique and required lookup headers are copied.")
    else:
        print("    BLOCKED — selector writing remains disabled.")
        if not unique:
            print("    - One or more C167 operand sites are missing or ambiguous.")
        if not header_ok:
            print("    - Current bank layout omits lookup header bytes used by Bosch axis-based routines.")
            print("      The next revision must copy selector-compatible blocks (header + axis + map),")
            print("      not only the axis/map payloads.")
    print("─"*72)


def print_v015_selector_layout(binary: bytes, maps):
    print("\n"+"═"*72)
    print(" V0.15 — SELECTOR-COMPATIBLE LAYOUT + KFZW REFERENCE SET")
    print("═"*72)

    # Header-inclusive source blocks required by Bosch axis lookup conventions.
    specs=[]
    lam=maps["LAMFA"]
    if lam.y_axis:
        specs.append(("LAMFA.Y", lam.y_axis.address-2, 2 + lam.y_axis.byte_length))
    l1=maps["LDRXN"]; l2=maps["LDRXNZK"]
    if l1.y_axis:
        specs.append(("LDRXN.Y", l1.y_axis.address-2, 2 + l1.y_axis.byte_length))
    if l2.y_axis:
        specs.append(("LDRXNZK.Y", l2.y_axis.address-2, 2 + l2.y_axis.byte_length))

    print("\nHeader-inclusive blocks required in every copied bank")
    for name,start,size in specs:
        raw=binary[start:start+size]
        print(f"[ OK ] {name:<10} 0x{start:06X}-0x{start+size-1:06X}  {size:3d} B  "
              f"header={raw[:2].hex(' ').upper()}")

    print("\nKFZW / KFZW2 executable reference set")
    for name in ("KFZW","KFZW2"):
        m=maps[name]
        hits=_find_operand_instructions(binary,m.pattern_location,m.address,160)
        print(f"\n{name} map 0x{m.address:06X}: {len(hits)} direct operand references near paired lookup")
        for i,(p,b,dpp,op) in enumerate(hits,1):
            lo=max(0,p-16); hi=min(len(binary),p+28)
            ctx=binary[lo:hi]
            print(f"  [{i}] instr=0x{p:06X} bytes={' '.join(f'{x:02X}' for x in b)} "
                  f"DPP=0x{dpp:03X} operand=0x{op:04X}")
            print(f"      context 0x{lo:06X}: {' '.join(f'{x:02X}' for x in ctx)}")

    print("\nShared-axis note")
    print("  KFZW/KFZW2 share the validated X/Y axes in this BIN.")
    print("  The copied-bank selector must therefore keep one coherent axis set per bank")
    print("  and redirect every validated map reference belonging to the paired lookup.")

    print("\nV0.15 VERDICT")
    print("  ANALYSIS READY — header-inclusive bank blocks are defined and all nearby")
    print("  KFZW/KFZW2 direct operand references are enumerated.")
    print("  Selector writing remains disabled in this revision.")
    print("═"*72)


def print_v016_kfzw_flow(binary: bytes, maps):
    print("\n"+"═"*72)
    print(" V0.16 — KFZW/KFZW2 REFERENCE-SET FLOW")
    print("═"*72)
    k1=maps["KFZW"]; k2=maps["KFZW2"]
    h1=_find_operand_instructions(binary,k1.pattern_location,k1.address,192)
    h2=_find_operand_instructions(binary,k2.pattern_location,k2.address,192)
    refs=[]
    for p,b,dpp,op in h1:
        refs.append((p,"KFZW",b,dpp,op))
    for p,b,dpp,op in h2:
        refs.append((p,"KFZW2",b,dpp,op))
    refs.sort()

    for p,name,b,dpp,op in refs:
        print(f"  0x{p:06X}  {name:<5}  {' '.join(f'{x:02X}' for x in b)}  operand=0x{op:04X}")

    # Pair each KFZW2 reference with the next KFZW reference in the same short branch.
    pairs=[]
    for p2,n2,b2,dpp2,op2 in refs:
        if n2!="KFZW2":
            continue
        candidates=[r for r in refs if r[1]=="KFZW" and 0 < r[0]-p2 <= 0x40]
        if candidates:
            p1,n1,b1,dpp1,op1=min(candidates,key=lambda r:r[0])
            pairs.append((p2,p1,p1-p2))
    print("\nDetected lookup branches")
    used=set()
    for i,(p2,p1,dist) in enumerate(pairs,1):
        used.update((p2,p1))
        lo=max(0,p2-12); hi=min(len(binary),p1+36)
        print(f"[ OK ] Branch {i}: KFZW2 @0x{p2:06X} -> KFZW @0x{p1:06X}  delta=0x{dist:X}")
        print(f"       context 0x{lo:06X}: {' '.join(f'{x:02X}' for x in binary[lo:hi])}")
    extras=[r for r in refs if r[0] not in used]
    if extras:
        print("\nUnpaired references")
        for p,name,b,_,_ in extras:
            print(f"[WARN] 0x{p:06X} {name} {' '.join(f'{x:02X}' for x in b)}")
    else:
        print("\n[ OK ] All KFZW/KFZW2 references belong to paired lookup branches.")

    print("\nSelector implication")
    print("  Both branches must be redirected coherently. Patching only one occurrence")
    print("  would leave part of the ignition lookup on the stock calibration.")
    print("═"*72)

def print_v016_layout(layouts):
    print("\n"+"═"*72)
    print(" V0.16 — SELECTOR-COMPATIBLE BANK LAYOUT")
    print("═"*72)
    for bl in layouts:
        print(f"\nBANK {bl.bank}: 0x{bl.start:06X}-0x{bl.end:06X}")
        for c in bl.components:
            print(f"  {c.key:<18} {c.kind:<15} 0x{c.source:06X} -> 0x{c.dest:06X} ({c.size} B)")
    print("\nHeader compatibility")
    for bl in layouts:
        keys={c.key for c in bl.components}
        ok=all(k in keys for k in ("LAMFA.Y+HDR","LDRXN.BLOCK","LDRXNZK.BLOCK"))
        print(f"  Bank {bl.bank}: {'PASS' if ok else 'FAIL'}")
    print("═"*72)


@dataclass
class SelectorPatchSite:
    label: str
    instr: int
    original: bytes
    component_key: str
    source_target: int

def _component_dest(layout: BankLayout, key: str) -> int:
    for c in layout.components:
        if c.key == key:
            return c.dest
    raise MapSwitchError(f"Bank {layout.bank}: component {key} not found")

def _selector_target_for_bank(maps, layouts, bank: int, key: str) -> int:
    """Resolve exact address that an existing Bosch instruction conceptually points at."""
    if bank == 1:
        if key == "LAMFA":
            return maps["LAMFA"].address
        if key == "LAMFA.X":
            return maps["LAMFA"].x_axis.address
        if key == "LAMFA.Y+HDR":
            return maps["LAMFA"].y_axis.address - 2
        if key == "KFZW":
            return maps["KFZW"].address
        if key == "KFZW2":
            return maps["KFZW2"].address
        if key == "LDRXN.BLOCK":
            return maps["LDRXN"].y_axis.address - 2
        if key == "LDRXNZK.BLOCK":
            return maps["LDRXNZK"].y_axis.address - 2
        if key == "KRKTE":
            return maps["KRKTE"].address
        if key == "KFZWMN":
            return maps["KFZWMN"].address
        if key in ("KFNWEGM", "KFTVSA", "KFTVSAKAT"):
            return maps[key].address
        raise MapSwitchError(f"Unknown selector key {key}")

    bl = next((x for x in layouts if x.bank == bank), None)
    if bl is None:
        raise MapSwitchError(f"Bank {bank} layout missing")
    return _component_dest(bl, key)

def build_v017_patch_sites(binary: bytes, maps):
    sites=[]

    def one(label, key, target, anchor):
        hits=_find_operand_instructions(binary,anchor,target,192)
        if len(hits)!=1:
            raise MapSwitchError(f"{label}: expected one operand site, got {len(hits)}")
        p,b,_,_=hits[0]
        sites.append(SelectorPatchSite(label,p,b,key,target))

    lam=maps["LAMFA"]
    one("LAMFA map","LAMFA",lam.address,lam.pattern_location)
    one("LAMFA X","LAMFA.X",lam.x_axis.address,lam.pattern_location)
    one("LAMFA Y header","LAMFA.Y+HDR",lam.y_axis.address-2,lam.pattern_location)

    # KFZW/KFZW2 are intentionally multi-reference: all validated references
    # from the paired lookup branches become selector sites.
    for name,key in (("KFZW2","KFZW2"),("KFZW","KFZW")):
        m=maps[name]
        hits=_find_operand_instructions(binary,m.pattern_location,m.address,192)
        if len(hits)!=2:
            raise MapSwitchError(f"{name}: expected two paired operand sites, got {len(hits)}")
        for idx,(p,b,_,_) in enumerate(sorted(hits),1):
            sites.append(SelectorPatchSite(f"{name} branch {idx}",p,b,key,m.address))

    l1=maps["LDRXN"]; l2=maps["LDRXNZK"]
    one("LDRXN header","LDRXN.BLOCK",l1.y_axis.address-2,l1.pattern_location)
    one("LDRXNZK header","LDRXNZK.BLOCK",l2.y_axis.address-2,l2.pattern_location)

    kr=maps["KRKTE"]
    one("KRKTE scalar","KRKTE",kr.address,kr.pattern_location)
    return sorted(sites,key=lambda x:x.instr)

def print_v017_selector_patch_plan(binary: bytes, maps, layouts, banks: int):
    print("\n"+"═"*72)
    print(" V0.17 — COMPLETE SELECTOR PATCH PLAN")
    print("═"*72)

    sites=build_v017_patch_sites(binary,maps)
    print(f"Validated executable patch sites : {len(sites)}")
    print(f"Configured banks                 : {banks}")
    print(f"Selector state                   : current_bank RAM (v0.34 runtime; software-validated)")

    print("\nExecutable sites and saved original bytes")
    for st in sites:
        print(f"  0x{st.instr:06X}  {st.label:<20} "
              f"{' '.join(f'{x:02X}' for x in st.original)}  -> {st.component_key}")

    print("\nPer-bank target matrix")
    keys=[]
    for st in sites:
        if st.component_key not in keys:
            keys.append(st.component_key)
    for key in keys:
        vals=[]
        for bank in range(1,banks+1):
            addr=_selector_target_for_bank(maps,layouts,bank,key)
            base,dpp,op=_flash_operand(addr)
            vals.append((bank,addr,dpp,op))
        print(f"\n  {key}")
        for bank,addr,dpp,op in vals:
            print(f"    Bank {bank}: target=0x{addr:06X}  DPP=0x{dpp:03X} operand=0x{op:04X}")

    print("\nDPP transition analysis")
    changed=0
    for key in keys:
        dpps=[]
        for bank in range(1,banks+1):
            addr=_selector_target_for_bank(maps,layouts,bank,key)
            _,dpp,_=_flash_operand(addr)
            dpps.append(dpp)
        if len(set(dpps))>1:
            changed+=1
            print(f"  {key:<16}: " + " -> ".join(f"B{i+1}=0x{d:03X}" for i,d in enumerate(dpps)))
    if changed:
        print("\n  IMPORTANT:")
        print("  Copied banks are in another DPP window. A selector cannot be implemented")
        print("  by changing only the 16-bit operand in the original instruction.")
        print("  The trampoline must select BOTH the bank-specific DPP and operand/address,")
        print("  while preserving the original destination register and surrounding flow.")

    print("\nPatch policy for the next revision")
    print("  1. Verify every 4-byte original instruction before writing.")
    print("  2. Replace each validated site with a CALLS/JMP trampoline only when the")
    print("     surrounding instruction boundary permits a safe 4-byte redirect.")
    print("  3. Trampoline reads current_bank and selects the matching full address/DPP.")
    print("  4. Preserve the original destination register (R4/R12/etc.) per site.")
    print("  5. Return to the instruction immediately following the patched site.")
    print("  6. Abort the entire build if any original bytes differ from this plan.")

    print("\nV0.17 VERDICT")
    print("  PATCH PLAN COMPLETE — all selector targets and executable references are")
    print("  enumerated. No selector code is written by v0.17.")
    print("═"*72)


def _decode_calls_at(binary: bytes, pos: int):
    if pos < 0 or pos+4 > len(binary) or binary[pos] != 0xDA:
        return None
    bank=binary[pos+1]
    lo=binary[pos+2]
    hi=binary[pos+3]
    return ((bank<<16)|(hi<<8)|lo) & 0xFFFFF

def _find_calls_after(binary: bytes, start: int, distance: int = 40):
    out=[]
    end=min(len(binary)-4,start+distance)
    p=start
    while p<=end:
        if binary[p]==0xDA:
            out.append((p,bytes(binary[p:p+4]),_decode_calls_at(binary,p)))
            p+=4
        else:
            p+=1
    return out

def _all_direct_operand_sites(binary: bytes, target: int):
    """Enumerate direct 16-bit operand instructions for the target's DPP window."""
    _,dpp,op=_flash_operand(target)
    lo=op&0xFF; hi=(op>>8)&0xFF
    allowed={0xE6,0xF2,0xC2,0xF6}
    out=[]
    for p in range(0,len(binary)-3):
        if binary[p] in allowed and binary[p+2]==lo and binary[p+3]==hi:
            out.append((p,bytes(binary[p:p+4]),dpp,op))
    return out

def _reg_from_4byte_instruction(b: bytes):
    if len(b)!=4:
        return "?"
    # In the ME7 C167 sequences used here, byte1 low nibble identifies the
    # destination/source working register for E6/F2/F6.
    if b[0] in (0xE6,0xF2,0xF6):
        return f"R{b[1]&0x0F}"
    return f"op{b[0]:02X}"

def print_v018_reference_strategy(binary: bytes, maps):
    print("\n"+"═"*72)
    print(" V0.18 — REFERENCE MULTIMAP STRATEGY PREFLIGHT")
    print("═"*72)
    print("Strategy: hook lookup CALLS where possible; do not blindly replace every")
    print("map-pointer MOV. Enumerate scalar reads separately when no lookup CALL exists.")

    print("\nKFZW / KFZW2 lookup CALLS")
    ksites=[]
    for name in ("KFZW2","KFZW"):
        m=maps[name]
        refs=_find_operand_instructions(binary,m.pattern_location,m.address,192)
        for p,b,_,_ in refs:
            calls=_find_calls_after(binary,p+4,24)
            first=calls[0] if calls else None
            ksites.append((p,name,b,first))
    ksites.sort()
    call_positions=[]
    for p,name,b,call in ksites:
        if call:
            cp,cb,target=call
            call_positions.append(cp)
            print(f"[ OK ] {name:<5} setup @0x{p:06X} -> CALLS @0x{cp:06X} "
                  f"{' '.join(f'{x:02X}' for x in cb)} target=0x{target:06X}")
        else:
            print(f"[FAIL] {name:<5} setup @0x{p:06X}: no CALLS found immediately downstream")
    uniq_calls=sorted(set(call_positions))
    print(f"  Unique ignition lookup call sites: {len(uniq_calls)}")
    for p in uniq_calls:
        print(f"    0x{p:06X}")

    print("\nLDRXN / LDRXNZK lookup flow")
    la=maps["LDRXNZK"].pattern_location
    l_calls=_find_calls_after(binary,la,72)
    if l_calls:
        for p,b,target in l_calls:
            print(f"[ OK ] CALLS @0x{p:06X} {' '.join(f'{x:02X}' for x in b)} target=0x{target:06X}")
    else:
        print("[FAIL] No downstream CALLS found around paired LDR lookup anchor")

    print("\nLAMFA lookup flow")
    lm=maps["LAMFA"]
    lm_calls=_find_calls_after(binary,lm.pattern_location,96)
    if lm_calls:
        for p,b,target in lm_calls:
            print(f"[ OK ] CALLS @0x{p:06X} {' '.join(f'{x:02X}' for x in b)} target=0x{target:06X}")
    else:
        print("[FAIL] No CALLS found around exact LAMFA XML site")

    print("\nKRKTE direct-read inventory")
    kr=maps["KRKTE"]
    krrefs=_all_direct_operand_sites(binary,kr.address)
    # Keep likely direct reads: F2 = MOV reg,[mem] in the observed Bosch code.
    reads=[x for x in krrefs if x[1][0]==0xF2]
    for p,b,dpp,op in reads:
        print(f"[ OK ] 0x{p:06X}  {' '.join(f'{x:02X}' for x in b)}  "
              f"dest={_reg_from_4byte_instruction(b)} DPP=0x{dpp:03X} operand=0x{op:04X}")
    print(f"  KRKTE direct F2 read sites: {len(reads)}")
    if len(reads)>1:
        print("  IMPORTANT: KRKTE is consumed at multiple executable sites/registers;")
        print("  one scalar hook is insufficient. Generate a register-specific wrapper")
        print("  for every validated read or hook a common upstream path if proven.")

    print("\nReference-strategy verdict")
    ok=(len(uniq_calls)>=2 and bool(l_calls) and bool(lm_calls) and bool(reads))
    if ok:
        print("  PASS — lookup-call strategy can be derived from this BIN.")
        print("  The next BENCH revision should hook these CALLS/read sites, not the")
        print("  ten raw pointer-load instructions from v0.17.")
    else:
        print("  INCOMPLETE — one or more lookup/read paths still require analysis.")
    print("═"*72)


# ============================================================
# v0.19 BENCH SELECTORS
# ============================================================

R2=0xF2
R4=0xF4
R5=0xF5
R12=0xFC
R13=0xFD
R14=0xFE

@dataclass
class BenchSelectorHook:
    name: str
    address: int
    original: bytes
    wrapper_name: str
    patch_size: int = 4

@dataclass
class BenchSelectorBuild:
    cave: int
    size: int
    wrappers: dict[str,tuple[int,bytes]]
    hooks: list[BenchSelectorHook]
    helper_far_lookup: Optional[int]

def _raw_calls_target(binary: bytes, pos:int) -> tuple[bytes,int]:
    if pos < 0 or pos+4 > len(binary) or binary[pos] != 0xDA:
        raise MapSwitchError(f"Expected CALLS at 0x{pos:06X}")
    raw=bytes(binary[pos:pos+4])
    target=_decode_calls_at(binary,pos)
    if target is None:
        raise MapSwitchError(f"Could not decode CALLS at 0x{pos:06X}")
    return raw,target

def _emit_mov_reg_mem(a: _C167Builder, reg: int, addr: int):
    operand = _ram_word(addr)
    _emit_extp(a, addr >> 14)
    a.emit(0xF2, reg, _le16(operand))

def _emit_mov_reg_imm(a:_C167Builder, reg:int, value:int):
    a.emit(0xE6,reg,_le16(value))

def _emit_cmp_reg_small(a: _C167Builder, regnum: int, value: int):
    if not (0 <= regnum <= 15 and 0 <= value <= 7):
        raise MapSwitchError("CMP immediate encoding requires R0..R15 and #0..7")
    a.emit(0x48, (regnum << 4) | value)

def _emit_extp(a:_C167Builder, dpp:int):
    a.emit(0xD7,0x40,_le16(dpp))

def _emit_mov_reg_extp(a:_C167Builder, reg:int, dpp:int, operand:int):
    _emit_extp(a,dpp)
    a.emit(0xF2,reg,_le16(operand))

def _emit_rets(a:_C167Builder):
    a.emit(0xDB,0x00)

def _emit_call_raw(a:_C167Builder, raw:bytes):
    if len(raw)!=4 or raw[0]!=0xDA:
        raise MapSwitchError("Invalid raw CALLS bytes")
    a.emit(raw)

def _bank_targets(maps,layouts,banks,key):
    return [_selector_target_for_bank(maps,layouts,b,key) for b in range(1,banks+1)]

def _branch_on_bank(a: _C167Builder, regnum: int, banks: int, prefix: str):
    if not MIN_BANKS <= banks <= MAX_BANKS:
        raise MapSwitchError("Banks must be 2..4")
    for bank in range(1, banks + 1):
        _emit_cmp_reg_small(a, regnum, bank)
        a.jmpr(CC_Z, f"{prefix}_bank{bank}")
    a.jmpr(CC_UC, f"{prefix}_bank1")

def _finish_padded(a:_C167Builder, align:int=16)->bytes:
    code=a.finish()
    while len(code)%align:
        code += b'\xFF'
    return code

def build_kfzw_selector(*, name, banks, ram_base, targets,
                        original_lookup_call, helper_call, stride_address=None):
    if stride_address is None:
        raise MapSwitchError('Ignition selector requires a validated stride header')
    a=_C167Builder()
    a.emit(0xEC,0x88,0xEC,R4)  # Preserve caller PSW and scratch register.
    _emit_checked_bank(a,R4,ram_base)
    _branch_on_bank(a,4,banks,name)
    for bank in range(1,banks+1):
        a.label(f'{name}_bank{bank}')
        a.emit(0xFC,R4,0xFC,0x88)
        if bank==1:
            _emit_call_raw(a,original_lookup_call)
            _emit_rets(a)
            continue
        # The far helper uses R10/R11 where the near helper uses R14/R15.
        # Save those extra clobbers and reserve a final PSW slot below them.
        a.emit(0xEC,0x88,0xEC,0xFA,0xEC,0xFB)
        a.emit(0x88,0xF0,0x88,0xE0)
        _,page,operand=_flash_operand(targets[bank-1])
        _,stride_page,stride_operand=_flash_operand(stride_address)
        _emit_mov_reg_imm(a,R12,operand)
        _emit_mov_reg_imm(a,R13,page)
        _emit_mov_reg_imm(a,R14,stride_operand)
        _emit_mov_reg_imm(a,0xFF,stride_page)
        _emit_call_raw(a,helper_call)
        a.emit(0xEC,0x88)  # Capture lookup flags before MOV/stack cleanup.
        a.emit(0xF0,0xDE,0xF0,0xEA,0xF0,0xFB)
        a.emit(0xFC,0xFA)  # R10 = captured PSW, saved original R10 still on SP.
        a.emit(0xF2,0xFB,0x12,0xFE)  # R11 = SP (system stack, not R0).
        a.emit(0xC4,0xAB,0x04,0x00)  # Replace reserved final PSW slot.
        a.emit(0x08,0x04)  # Discard the two software-stack input words.
        a.emit(0xFC,0xFB,0xFC,0xFA,0xFC,0x88)
        _emit_rets(a)
    return _finish_padded(a)


def build_ldr_selector(*,name:str,banks:int,ram_base:int,targets:list[int],
                       original_lookup_call:bytes)->bytes:
    """DR LDR selector.

    R12/R13 are the live Bosch lookup pointer pair at the original CALL site.
    Therefore they MUST NOT be used as bank-selection scratch.  R4 is used for
    current_bank (same strategy as the known working reference selector).
    Bank 1 keeps the already prepared stock R12/R13. Added banks replace only
    R12/R13, preserving the other live lookup inputs.
    """
    a=_C167Builder()
    a.emit(0xEC,0x88,0xEC,R4)
    BANK=ram_base
    _emit_checked_bank(a,R4,BANK)
    _branch_on_bank(a,4,banks,name)

    for bank in range(1,banks+1):
        a.label(f"{name}_bank{bank}")
        a.emit(0xFC,R4,0xFC,0x88)
        if bank != 1:
            _,dpp,op=_flash_operand(targets[bank-1])
            _emit_mov_reg_imm(a,R12,op)
            _emit_mov_reg_imm(a,R13,dpp)
        _emit_call_raw(a,original_lookup_call)
        _emit_rets(a)
    return _finish_padded(a)


def build_lamfa_selector(*,banks:int,ram_base:int,maps,layouts,
                         original_lookup_call:bytes)->bytes:
    """LAMFA selector reproducing the COMPLETE stock Bosch lookup setup.

    Stock DR sequence before CALLS 0x0266C2 includes:
      map pointer pushed as (offset,DPP)
      X-axis pointer pushed as (offset,DPP)
      EXTP Y-DPP
      C2 FC <Y header byte 0>
      R13 = Y payload offset
      R14 = Y DPP
      EXTP Y-DPP
      C2 FF <Y header byte 1>
      CALLS lookup

    Earlier revisions rebuilt map/X/Y pointers but omitted the two C2 header
    loads.  v0.23 restores them for every bank, including Bank 1.
    """
    a=_C167Builder()
    a.emit(0xEC,0x88,0xEC,R4)
    BANK=ram_base

    # current_bank selection. R4 is rebuilt in every branch before lookup.
    _emit_checked_bank(a,R4,BANK)
    _branch_on_bank(a,4,banks,"lamfa")

    for bank in range(1,banks+1):
        a.label(f"lamfa_bank{bank}")
        a.emit(0xFC,R4,0xFC,0x88)

        map_addr=_selector_target_for_bank(maps,layouts,bank,"LAMFA")
        x_addr=_selector_target_for_bank(maps,layouts,bank,"LAMFA.X")
        y_hdr=_selector_target_for_bank(maps,layouts,bank,"LAMFA.Y+HDR")
        y_payload=y_hdr+2

        _,mdpp,mop=_flash_operand(map_addr)
        _,xdpp,xop=_flash_operand(x_addr)
        _,hdpp,hop=_flash_operand(y_hdr)
        _,ydpp,yop=_flash_operand(y_payload)

        if len({mdpp,xdpp,hdpp,ydpp}) != 1:
            raise MapSwitchError(
                f"LAMFA bank {bank}: map/X/Y header/Y payload do not share DPP"
            )

        # Same stack/pointer setup as the stock LAMFA code.
        a.emit(0x06,0xF0,0x08,0x00)      # add r0,#8

        _emit_mov_reg_imm(a,R4,mop)
        _emit_mov_reg_imm(a,R5,mdpp)
        a.emit(0x88,0x50,0x88,0x40)      # push DPP, offset

        _emit_mov_reg_imm(a,R4,xop)
        _emit_mov_reg_imm(a,R5,xdpp)
        a.emit(0x88,0x50,0x88,0x40)      # push DPP, offset

        # Restore the two Bosch Y-axis header reads omitted by v0.21/v0.22.
        _emit_extp(a,hdpp)
        a.emit(0xC2,R12,_le16(hop))       # stock: C2 FC <header+0>

        _emit_mov_reg_imm(a,R13,yop)
        _emit_mov_reg_imm(a,R14,ydpp)

        _emit_extp(a,hdpp)
        a.emit(0xC2,0xFF,_le16((hop+1) & 0xFFFF))  # stock: C2 FF <header+1>

        _emit_call_raw(a,original_lookup_call)
        _emit_rets(a)

    return _finish_padded(a)


def build_krkte_selector(*,name,regbyte,regnum,banks,ram_base,targets):
    a=_C167Builder()
    a.emit(0xEC,0x88)
    _emit_checked_bank(a,regbyte,ram_base)
    _branch_on_bank(a,regnum,banks,name)
    for bank in range(1,banks+1):
        a.label(f'{name}_bank{bank}')
        a.emit(0xFC,0x88)  # MOV must retain the incoming C/V flags.
        _,page,operand=_flash_operand(targets[bank-1])
        _emit_mov_reg_extp(a,regbyte,page,operand)
        _emit_rets(a)
    return _finish_padded(a)


def _near_ptr_candidates_for_flash(addr:int) -> tuple[int,int,int,int]:
    """All C167 16-bit near-pointer encodings for a physical address offset.

    The top two bits select DPP0..DPP3; the low 14 bits are the page offset.
    The stock hook is discovered from executable code, so no DPP register
    number or software-specific address is assumed here.
    """
    off=addr & 0x3FFF
    return tuple(off | sel for sel in (0x0000,0x4000,0x8000,0xC000))


def _find_kfzwmn_nearptr_hook(binary:bytes, map_addr:int):
    if not 30<=map_addr<map_addr+192<=len(binary):
        raise MapSwitchError('KFZWMN: invalid map/header range')
    if binary[map_addr-30:map_addr-28]!=b'\x10\x0C':
        raise MapSwitchError('KFZWMN: unsupported header dimensions')
    candidates=[]
    for logical in _near_ptr_candidates_for_flash(map_addr):
        # Same Audi DPP0/DPP1 near addressing as the core ignition code.
        if not 0<=logical<0x8000 or _linear_from_dpp_word(logical)!=map_addr:
            continue
        expected=(b'\xE6\xF4'+_le16(logical)+b'\x88\x40'+
                  b'\xC2\xFC'+_le16(logical-30)+b'\xE6\xFD'+_le16(logical-28)+
                  b'\xC2\xFE'+_le16(logical-29)+b'\xE6\xFF'+_le16(logical-12))
        rx=re.compile(re.escape(expected)+rb'\xDA[\x00\x80-\x8F]..\x08\x06',re.S)
        for match in rx.finditer(binary):
            if match.start()&1:
                continue
            pos=match.start();cp=pos+len(expected)
            raw,target=_raw_calls_target(binary,cp)
            candidates.append((pos,bytes(binary[pos:pos+4]),logical,(cp,raw,target)))
    if len(candidates)!=1:
        raise MapSwitchError(f'KFZWMN near-pointer hook: expected exactly 1 complete setup, got {len(candidates)}')
    return candidates[0]


def _find_kfnwegm_lookup_hook(binary:bytes, map_addr:int):
    """Find a complete far BYTE lookup with stack inputs and stride pointer."""
    _,page,operand=_flash_operand(map_addr)
    setup=b'\xE6\xFC'+_le16(operand)+b'\xE6\xFD'+_le16(page)
    rx=re.compile(re.escape(setup)+rb'\xE6\xFE..\xE6\xFF..\xDA[\x80-\x8F]..\x08\x04',re.S)
    # Complete Bosch unsigned BYTE interpolator; addresses may relocate.
    signature=bytes.fromhex(
        'd4 a0 00 00 d4 b0 02 00 dc 4f a9 4e c0 4e f0 1a c0 31 1b 1e '
        'f2 f1 0e fe 00 c1 f0 1b c0 31 00 c1 dc 4d a9 4c c0 42 5c 8b '
        '7c 1b 2d 0a dc 4d f4 6c 01 00 c0 63 20 32 5c 13 0b 3b f2 f3 '
        '0c fe 00 23 f0 42 5c 8a 7c 1a 2d 15 00 ce dc 4d 99 4c c0 42 '
        '78 b0 2d 09 dc 4d a9 6c c0 63 20 32 5c 13 0b 3b f2 f3 0c fe '
        '00 23 20 24 5c 12 0b 2a f2 f2 0c fe 00 42 db 00')
    hits=[]
    for match in rx.finditer(binary):
        if match.start()&1:
            continue
        pos=match.start();raw,target=_raw_calls_target(binary,pos+16)
        if binary[target:target+len(signature)]!=signature:
            raise MapSwitchError('Pops lookup: unsupported unsigned BYTE interpolator')
        hits.append((pos,bytes(binary[pos:pos+8]),(pos+16,raw,target)))
    if len(hits)!=1:
        raise MapSwitchError(f'R12/R13 lookup for 0x{map_addr:06X}: expected 1 complete setup, got {len(hits)}')
    return hits[0]


def build_kfzwmn_nearptr_selector(*,banks:int,ram_base:int,physical_targets:list[int],
                                  stock_logical_ptr:int)->bytes:
    """Replace MOV R4,#near(map), preserving its incoming C/V flags."""
    if len(physical_targets)!=banks:
        raise MapSwitchError('KFZWMN: bank target count mismatch')
    base=physical_targets[0]&~0x3FFF
    if any((x&~0x3FFF)!=base or (x+191)&~0x3FFF!=base for x in physical_targets):
        raise MapSwitchError('KFZWMN copies must fit wholly in the stock DPP page')
    if stock_logical_ptr&0x3FFF!=physical_targets[0]&0x3FFF:
        raise MapSwitchError('KFZWMN: original pointer does not match Bank 1')
    logical=[(stock_logical_ptr&0xC000)|(x&0x3FFF) for x in physical_targets]
    a=_C167Builder()
    a.emit(0xEC,0x88)
    _emit_checked_bank(a,R4,ram_base)
    _branch_on_bank(a,4,banks,'kfzwmn')
    for bank in range(1,banks+1):
        a.label(f'kfzwmn_bank{bank}')
        a.emit(0xFC,0x88)
        _emit_mov_reg_imm(a,R4,logical[bank-1])
        _emit_rets(a)
    return _finish_padded(a)


def build_kfnwegm_selector(*,banks:int,ram_base:int,targets:list[int],
                           original_lookup_call:bytes)->bytes:
    """Keep R0 stack inputs and R14/R15 stride pointer; switch R12/R13 only."""
    if len(targets)!=banks:
        raise MapSwitchError('Pops: bank target count mismatch')
    a=_C167Builder()
    a.emit(0xEC,0x88,0xEC,R4)
    _emit_checked_bank(a,R4,ram_base)
    _branch_on_bank(a,4,banks,'kfnwegm')
    for bank in range(1,banks+1):
        a.label(f'kfnwegm_bank{bank}')
        a.emit(0xFC,R4,0xFC,0x88)
        if bank!=1:
            _,page,operand=_flash_operand(targets[bank-1])
            _emit_mov_reg_imm(a,R12,operand)
            _emit_mov_reg_imm(a,R13,page)
        _emit_call_raw(a,original_lookup_call)
        _emit_rets(a)
    return _finish_padded(a)


def _find_far_lookup_helper(binary:bytes)->tuple[int,bytes]:
    """Locate the Bosch far-map helper by code signature, not by HJ address.

    The known working HJ software has this routine at 0x026294, while the DR
    software used during development contains the same routine at 0x0262AC.
    Therefore the executable function body is the invariant; its address is not.
    """
    sig=bytes.fromhex('d4 a0 00 00 d4 b0 02 00 dc 4f a9 4e c0 4e f0 1a c0 31 1b 1e f2 f1 0e fe 00 c1 f0 1b c0 31 00 c1 dc 4d a9 4c d0 42 5c 8b 7c 1b 2d 0a dc 4d f4 6c 01 00 d0 63 20 32 5c 13 0b 3b f2 f3 0c fe 00 23 f0 42 5c 8a 7c 1a 2d 15 00 ce dc 4d 99 4c d0 42 78 b0 2d 09 dc 4d a9 6c d0 63 20 32 5c 13 0b 3b f2 f3 0c fe 00 23 20 24 5c 12 0b 2a f2 f2 0c fe 00 42 db 00')
    hits=[]
    p=0
    while True:
        p=binary.find(sig,p)
        if p<0:
            break
        hits.append(p)
        p+=1
    if len(hits)!=1:
        raise MapSwitchError(
            f"Bosch far-map helper signature: expected exactly 1 match, got {len(hits)}"
        )
    target=hits[0]
    return target,b'\xDA'+_calls_operand(target)


def _selector_reserved(layouts,rt):
    reserved=[(bl.start,bl.end+1) for bl in layouts]
    reserved.extend((c.dest,c.dest+c.size) for bl in layouts for c in bl.components)
    reserved.extend((c.dest,c.dest+c.size) for bl in layouts for c in bl.components)
    if rt.code_cave is not None:
        reserved.append((rt.code_cave,rt.code_cave+rt.code_cave_size))
    return reserved

def _find_selector_cave(data:bytes,size:int,reserved:list[tuple[int,int]])->Optional[int]:
    # Prefer a traditional 0xBxxxx code region, then fall back to the general
    # upper-flash FF allocator.
    for start in (0xB0000,0x70000):
        pos=start
        while pos+size <= len(data):
            idx=data.find(b'\xFF'*size,pos)
            if idx<0: break
            if (idx & 0xFFFF)+size > 0x10000:
                pos=align_up(idx+1,0x10000); continue
            if idx%0x10:
                pos=idx+(0x10-(idx%0x10)); continue
            if all(not ranges_overlap(idx,idx+size,a,b) for a,b in reserved):
                return idx
            pos=idx+0x10
    return None

def build_bench_selectors(binary:bytes,maps,layouts,banks:int,rt:RuntimePreflight)->BenchSelectorBuild:
    if not rt.ready_for_codegen or rt.ram_base is None:
        raise MapSwitchError("Runtime pre-flight must PASS before selector generation")

    helper_target,helper_call=_find_far_lookup_helper(binary)

    krefs,ignition_call,l_calls,lamfa_call,kr_reads = _validated_selector_references(binary,maps)
    reg_map={0xF2:(R2,2),0xF4:(R4,4),0xF5:(R5,5)}

    wrappers={}
    wrappers["KFZW"]=build_kfzw_selector(
        name="kfzw",banks=banks,ram_base=rt.ram_base,
        targets=_bank_targets(maps,layouts,banks,"KFZW"),
        original_lookup_call=ignition_call,helper_call=helper_call,
        stride_address=maps["KFZW"].x_axis.address-2)
    wrappers["KFZW2"]=build_kfzw_selector(
        name="kfzw2",banks=banks,ram_base=rt.ram_base,
        targets=_bank_targets(maps,layouts,banks,"KFZW2"),
        original_lookup_call=ignition_call,helper_call=helper_call,
        stride_address=maps["KFZW"].x_axis.address-2)

    # First pair setup in the validated stream is LDRXNZK, second LDRXN.
    wrappers["LDRXNZK"]=build_ldr_selector(
        name="ldrxnzk",banks=banks,ram_base=rt.ram_base,
        targets=_bank_targets(maps,layouts,banks,"LDRXNZK.BLOCK"),
        original_lookup_call=l_calls[0][1])
    wrappers["LDRXN"]=build_ldr_selector(
        name="ldrxn",banks=banks,ram_base=rt.ram_base,
        targets=_bank_targets(maps,layouts,banks,"LDRXN.BLOCK"),
        original_lookup_call=l_calls[1][1])
    wrappers["LAMFA"]=build_lamfa_selector(
        banks=banks,ram_base=rt.ram_base,maps=maps,layouts=layouts,
        original_lookup_call=lamfa_call[1])

    # KFZWMN: patch the stock MOV R4,#near(map) itself. The wrapper returns
    # with R4 containing the selected bank's near pointer; stock push/axis
    # setup and stock lookup remain untouched.
    kfzwmn_hook=None
    if maps.get("KFZWMN") is not None:
        kp,kraw,klogical,kcall=_find_kfzwmn_nearptr_hook(binary,maps["KFZWMN"].address)
        wrappers["KFZWMN"]=build_kfzwmn_nearptr_selector(
            banks=banks,ram_base=rt.ram_base,
            physical_targets=_bank_targets(maps,layouts,banks,"KFZWMN"),
            stock_logical_ptr=klogical)
        kfzwmn_hook=BenchSelectorHook("KFZWMN near ptr",kp,kraw,"KFZWMN",4)

    # KFNWEGM/KFTVSA/KFTVSAKAT: all three use the same Bosch R12/R13
    # explicit table-pointer lookup contract. Each hook is discovered from the
    # dynamically resolved map address, never from a software-specific code offset.
    pops_lookup_hooks=[]
    for runtime_name in ("KFNWEGM","KFTVSA","KFTVSAKAT"):
        if maps.get(runtime_name) is None:
            continue
        ks,ksetup,kcall=_find_kfnwegm_lookup_hook(binary,maps[runtime_name].address)
        cp,cb,target=kcall
        wrappers[runtime_name]=build_kfnwegm_selector(
            banks=banks,ram_base=rt.ram_base,
            targets=_bank_targets(maps,layouts,banks,runtime_name),
            original_lookup_call=cb)
        pops_lookup_hooks.append(
            BenchSelectorHook(f"{runtime_name} lookup",cp,cb,runtime_name,4)
        )

    kr_targets=_bank_targets(maps,layouts,banks,"KRKTE")
    kr_hooks=[]
    for p,b,dpp,op in kr_reads:
        regbyte=b[1]
        if regbyte not in reg_map:
            raise MapSwitchError(f"KRKTE read @0x{p:06X}: unsupported destination byte 0x{regbyte:02X}")
        rb,rn=reg_map[regbyte]
        wname=f"KRKTE_R{rn}"
        if wname not in wrappers:
            wrappers[wname]=build_krkte_selector(
                name=wname.lower(),regbyte=rb,regnum=rn,banks=banks,
                ram_base=rt.ram_base,targets=kr_targets)

        # If an EXTP immediately precedes the read, hook the 8-byte pair exactly
        # like the proven reference patch, otherwise hook the 4-byte F2 itself.
        if p>=4 and binary[p-4:p]==b"\xD7\x40"+_le16(dpp):
            hp=p-4; orig=bytes(binary[hp:hp+8]); psz=8
        else:
            hp=p; orig=bytes(binary[p:p+4]); psz=4
        kr_hooks.append(BenchSelectorHook(wname,hp,orig,wname,psz))

    # Lay wrappers into one aligned cave.
    order=["KFZW","KFZW2","LDRXNZK","LDRXN","LAMFA","KFZWMN","KFNWEGM","KFTVSA","KFTVSAKAT",
           "KRKTE_R5","KRKTE_R4","KRKTE_R2"]
    order=[x for x in order if x in wrappers]
    offsets={}
    cursor=0
    for name in order:
        cursor=align_up(cursor,0x10)
        offsets[name]=cursor
        cursor += len(wrappers[name])
    total=align_up(cursor,0x10)
    cave=_find_selector_cave(binary,total,_selector_reserved(layouts,rt))
    if cave is None:
        raise MapSwitchError(f"No selector FF cave of 0x{total:X} bytes found")

    placed={name:(cave+offsets[name],wrappers[name]) for name in order}

    hooks=[]
    for name in ("KFZW2","KFZW"):
        for idx,(cp,cb,target) in enumerate(krefs[name],1):
            hooks.append(BenchSelectorHook(f"{name} call {idx}",cp,cb,name,4))
    hooks.append(BenchSelectorHook("LDRXNZK call",l_calls[0][0],l_calls[0][1],"LDRXNZK",4))
    hooks.append(BenchSelectorHook("LDRXN call",l_calls[1][0],l_calls[1][1],"LDRXN",4))
    hooks.append(BenchSelectorHook("LAMFA call",lamfa_call[0],lamfa_call[1],"LAMFA",4))
    if kfzwmn_hook is not None:
        hooks.append(kfzwmn_hook)
    hooks.extend(pops_lookup_hooks)
    hooks.extend(kr_hooks)

    # Ensure hooks do not overlap.
    hs=sorted((h.address,h.address+h.patch_size,h.name) for h in hooks)
    for (a0,a1,n0),(b0,b1,n1) in zip(hs,hs[1:]):
        if ranges_overlap(a0,a1,b0,b1):
            raise MapSwitchError(f"Selector hooks overlap: {n0} and {n1}")

    return BenchSelectorBuild(cave,total,placed,hooks,helper_target)

def print_bench_selector_plan(build:BenchSelectorBuild,binary:bytes):
    print("\n"+"═"*72)
    print(" V0.25 — FULL MULTIMAP RUNTIME SELECTOR PLAN")
    print("═"*72)
    print(f"Selector cave       : 0x{build.cave:06X}-0x{build.cave+build.size-1:06X}")
    print(f"Bosch far-map helper: 0x{build.helper_far_lookup:06X} (located by complete 116-byte signature)")
    print("Ignition strategy   : Bank 1 stock CALLS; Bank 2..N far-map helper")
    print("\nWrappers")
    for name,(addr,code) in build.wrappers.items():
        print(f"  {name:<12} 0x{addr:06X}  {len(code):4d} B")
    print("\nHooks")
    for h in sorted(build.hooks,key=lambda x:x.address):
        dst=build.wrappers[h.wrapper_name][0]
        print(f"  0x{h.address:06X}  {h.name:<18} "
              f"orig={' '.join(f'{x:02X}' for x in h.original)} -> 0x{dst:06X}")
    print("\nSafety")
    print("  - every hook requires exact original-byte match")
    print("  - every wrapper cave byte must still be FF")
    print("  - runtime hook/RAM pre-flight must already PASS")
    print("  - LDR preserves stock R12/R13 on Bank 1; R4 is selector scratch")
    print("  - LAMFA reconstructs map/X + BOTH Y-header C2 loads + Y payload for every bank")
    print("  - KFZW/KFZW2 preserve context; copied banks use stack inputs + validated stride pointer")
    print("  - copied ignition banks call far-map helper then restore R0 by +4")
    print("  - Bosch far-map helper is located by function signature, not fixed address")
    print("  - KFZWMN selector replaces only its stock near-pointer MOV; stock lookup remains intact")
    print("  - KFZWMN copied banks are constrained to the stock 16 KiB DPP window")
    print("  - Pops far lookups preserve R0 stack inputs and R14/R15 stride pointer; switch R12/R13")
    print("  - build is BENCH ONLY; checksum correction is still external")
    print("═"*72)

def install_bench_selectors(data:bytearray,original:bytes,build:BenchSelectorBuild):
    # Verify all original hooks before changing anything.
    for h in build.hooks:
        cur=bytes(data[h.address:h.address+h.patch_size])
        # data may already contain only bank copies/runtime changes elsewhere;
        # hook sites must remain exactly original.
        if cur != h.original:
            raise MapSwitchError(
                f"{h.name} hook mismatch @0x{h.address:06X}: "
                f"expected {h.original.hex(' ').upper()}, got {cur.hex(' ').upper()}"
            )

    # Verify cave in the current output is untouched FF.
    if any(x!=0xFF for x in data[build.cave:build.cave+build.size]):
        raise MapSwitchError("Selector code cave is not FF at write time")

    # Write wrappers.
    for name,(addr,code) in build.wrappers.items():
        data[addr:addr+len(code)]=code
        if bytes(data[addr:addr+len(code)]) != code:
            raise MapSwitchError(f"{name} wrapper verification failed")

    # Patch hooks only after all code was written successfully.
    for h in build.hooks:
        dst=build.wrappers[h.wrapper_name][0]
        patch=b'\xDA'+_calls_operand(dst)
        if h.patch_size==4:
            data[h.address:h.address+4]=patch
        elif h.patch_size==8:
            data[h.address:h.address+8]=patch+b'\xCC\x00\xCC\x00'
        else:
            raise MapSwitchError(f"Unsupported hook size {h.patch_size}")
        expected=patch if h.patch_size==4 else patch+b'\xCC\x00\xCC\x00'
        if bytes(data[h.address:h.address+h.patch_size]) != expected:
            raise MapSwitchError(f"{h.name} hook write verification failed")

def bench_full_output_name(p:Path,banks:int):
    q=p.with_name(f"{p.stem}_MULTIMAP_{banks}BANK{p.suffix or '.bin'}"); n=2
    while q.exists():
        q=p.with_name(f"{p.stem}_MULTIMAP_{banks}BANK_{n}{p.suffix or '.bin'}"); n+=1
    return q


def _hx(v):
    return "" if v is None else f"0x{int(v):06X}"

def _audit_maps(audit:CsvAuditLog, maps):
    for name in REQUIRED_MAPS:
        m=maps.get(name)
        if m is None:
            audit.add("MAP","%s" % name,"FAIL",details="not found")
            continue
        audit.add("MAP",name,"PASS",address=_hx(m.address),
                  size_bytes=getattr(m,"byte_size",""),
                  details=f"shape={getattr(m,'shape','')}")
        for axis_name,axis in (("X",getattr(m,"x_axis",None)),("Y",getattr(m,"y_axis",None))):
            if axis is not None:
                audit.add("AXIS",f"{name}.{axis_name}","PASS",
                          address=_hx(axis.address),
                          size_bytes=getattr(axis,"byte_size",""),
                          details=f"points={getattr(axis,'points','')}")


def _audit_layouts(audit:CsvAuditLog, layouts):
    for bl in layouts:
        audit.add("BANK",f"Bank {bl.bank}","PASS",
                  address=f"0x{bl.start:06X}-0x{bl.end:06X}",bank=bl.bank,
                  size_bytes=bl.end-bl.start+1)
        for c in bl.components:
            audit.add("BANK_COMPONENT",c.key,"PASS",
                      source=_hx(c.source),destination=_hx(c.dest),
                      size_bytes=c.size,bank=bl.bank,details=c.kind)

def _audit_runtime(audit:CsvAuditLog, rt, switch_mode, tach_ticks):
    audit.add("RUNTIME","Scheduler hook","PASS" if rt.hook is not None else "FAIL",
              address=_hx(rt.hook),destination=_hx(rt.code_cave),
              details=f"continue={_hx(rt.continuation)}")
    audit.add("RUNTIME","RAM","PASS" if rt.ram_base is not None else "FAIL",
              address=_hx(rt.ram_base),size_bytes=getattr(rt,"ram_size",""),
              details=f"verdict={getattr(getattr(rt,'ram_candidate',None),'verdict','')}; switch={switch_mode}; tach_ticks={tach_ticks}")

def _audit_selectors(audit:CsvAuditLog, build):
    if build is None:
        audit.add("SELECTORS","Map selectors","NOT_REQUESTED")
        return
    audit.add("SELECTORS","Selector cave","PASS",
              address=f"0x{build.cave:06X}-0x{build.cave+build.size-1:06X}",
              size_bytes=build.size)
    audit.add("SELECTORS","Bosch far-map helper","PASS",
              address=_hx(build.helper_far_lookup))
    for name,(addr,code) in build.wrappers.items():
        audit.add("WRAPPER",name,"PASS",address=_hx(addr),size_bytes=len(code))
    for h in build.hooks:
        dst=build.wrappers[h.wrapper_name][0]
        audit.add("HOOK",h.name,"PASS",address=_hx(h.address),
                  destination=_hx(dst),size_bytes=h.patch_size,
                  details="orig="+h.original.hex(" ").upper())


def _map_shape_text(m):
    if m is None:
        return ""
    if m.x_axis is not None and m.y_axis is not None:
        return f"{m.x_axis.length}x{m.y_axis.length}"
    if m.x_axis is not None:
        return str(m.x_axis.length)
    if m.y_axis is not None:
        return str(m.y_axis.length)
    return str(m.length or 1)

def _map_meta_details(m):
    if m is None:
        return ""
    sign="signed" if m.signed else "unsigned"
    return (f"shape={_map_shape_text(m)}; width={m.width*8}-bit {sign}; "
            f"factor={m.factor:g}; offset={m.offset:g}; endian={m.endianness}")

def _bank1_components(maps):
    """Return the exact stock components mirrored by the copied bank layout."""
    out=[]
    lamfa=maps["LAMFA"]; kfzw=maps["KFZW"]; kfzw2=maps["KFZW2"]
    ldrxn=maps["LDRXN"]; ldrxzk=maps["LDRXNZK"]; krkte=maps["KRKTE"]

    # LAMFA copied layout: Y header+payload, X payload, map.
    out.append(("LAMFA.Y+HDR","axis-y+hdr",lamfa.y_axis.address-2,lamfa.y_axis.byte_length+2))
    out.append(("LAMFA.X","axis-x",lamfa.x_axis.address,lamfa.x_axis.byte_length))
    out.append(("LAMFA","map",lamfa.address,lamfa.byte_length))

    # KFZW/KFZW2 share axes.
    out.append(("KFZW.X","axis-x",kfzw.x_axis.address,kfzw.x_axis.byte_length))
    out.append(("KFZW.Y","axis-y",kfzw.y_axis.address,kfzw.y_axis.byte_length))
    out.append(("KFZW","map",kfzw.address,kfzw.byte_length))
    out.append(("KFZW2","map",kfzw2.address,kfzw2.byte_length))

    # LDR copied selector-compatible blocks include 2-byte header.
    out.append(("LDRXN.BLOCK","header+axis+map",ldrxn.y_axis.address-2,2+ldrxn.y_axis.byte_length+ldrxn.byte_length))
    out.append(("LDRXNZK.BLOCK","header+axis+map",ldrxzk.y_axis.address-2,2+ldrxzk.y_axis.byte_length+ldrxzk.byte_length))
    out.append(("KRKTE","map",krkte.address,krkte.byte_length))
    for name in EXPERIMENTAL_COPY_MAPS:
        if name in maps:
            m=maps[name]
            out.append((name,'map-runtime',m.address,m.byte_length))
    return out

def _xdf_math_equation(factor:float, offset:float=0.0) -> str:
    if abs(offset) < 1e-15:
        return f"{factor:g} * X"
    sign="+" if offset >= 0 else "-"
    return f"({factor:g} * X) {sign} {abs(offset):g}"

def _xdf_set_math(axis_el, factor:float, offset:float=0.0):
    math_el=axis_el.find("MATH")
    if math_el is None:
        math_el=ET.SubElement(axis_el,"MATH")
        ET.SubElement(math_el,"VAR",{"id":"X"})
    math_el.set("equation",_xdf_math_equation(factor,offset))

def _xdf_find_bank_component(layouts, bank:int, key:str):
    for bl in layouts:
        if bl.bank == bank:
            for c in bl.components:
                if c.key == key:
                    return c
    raise MapSwitchError(f"XDF: bank {bank} component {key} not found")

def _xdf_bank_addresses(maps, layouts, bank:int, map_name:str):
    """Return XDF addresses for a map in the selected bank."""
    m=maps[map_name]
    if bank>1 and map_name in ("KFZW","KFZW2"):
        z=_xdf_find_bank_component(layouts,bank,map_name)
        return {"x":m.x_axis.address,"y":m.y_axis.address,"z":z.dest}


    if bank == 1:
        if map_name in ("KFZW","KFZW2"):
            return {
                "x":m.x_axis.address,
                "y":m.y_axis.address,
                "z":m.address,
            }
        if map_name=="LAMFA":
            return {
                "x":m.x_axis.address,
                "y":m.y_axis.address,
                "z":m.address,
            }
        if map_name in ("LDRXN","LDRXNZK"):
            return {
                "x":m.y_axis.address,
                "z":m.address,
            }
        if map_name=="KRKTE":
            return {"z":m.address}

    if map_name=="LAMFA":
        yh=_xdf_find_bank_component(layouts,bank,"LAMFA.Y+HDR")
        xx=_xdf_find_bank_component(layouts,bank,"LAMFA.X")
        zz=_xdf_find_bank_component(layouts,bank,"LAMFA")
        return {"x":xx.dest,"y":yh.dest+2,"z":zz.dest}

    if map_name=="KFZW":
        xx=_xdf_find_bank_component(layouts,bank,"KFZW.X")
        yy=_xdf_find_bank_component(layouts,bank,"KFZW.Y")
        zz=_xdf_find_bank_component(layouts,bank,"KFZW")
        return {"x":xx.dest,"y":yy.dest,"z":zz.dest}

    if map_name=="KFZW2":
        xx=_xdf_find_bank_component(layouts,bank,"KFZW.X")
        yy=_xdf_find_bank_component(layouts,bank,"KFZW.Y")
        zz=_xdf_find_bank_component(layouts,bank,"KFZW2")
        return {"x":xx.dest,"y":yy.dest,"z":zz.dest}

    if map_name in ("LDRXN","LDRXNZK"):
        block=_xdf_find_bank_component(layouts,bank,f"{map_name}.BLOCK")
        # copied block = 2-byte header + axis + map
        return {
            "x":block.dest+2,
            "z":block.dest+2+m.y_axis.byte_length,
        }

    if map_name=="KRKTE":
        zz=_xdf_find_bank_component(layouts,bank,"KRKTE")
        return {"z":zz.dest}

    if map_name in EXPERIMENTAL_COPY_MAPS:
        if bank==1:
            return {"z":m.address}
        zz=_xdf_find_bank_component(layouts,bank,map_name)
        return {"z":zz.dest}

    raise MapSwitchError(f"XDF: unsupported map {map_name}")

def _xdf_axis_data(axis_el, located, address:int, *, count:int|None=None, meta:dict|None=None):
    data=axis_el.find("EMBEDDEDDATA")
    if data is None:
        data=ET.SubElement(axis_el,"EMBEDDEDDATA")
    n=located.length if count is None else count
    bits=(meta or {}).get("bits",located.width*8)
    data.set("mmedaddress",f"0x{address:X}")
    data.set("mmedelementsizebits",str(bits))
    data.set("mmedcolcount",str(n))
    data.set("mmedmajorstridebits",str(bits))
    data.set("mmedminorstridebits","0")

    # TunerPro expects a complete axis definition.  The embedded template
    # already has these nodes, but newly-created experimental tables did not;
    # that could make a table open as an empty/blank map.
    _xdf_set_text(axis_el,"indexcount",n)
    embed=axis_el.find("embedinfo")
    if embed is None:
        embed=ET.SubElement(axis_el,"embedinfo")
    embed.set("type","1")
    _xdf_set_text(axis_el,"datatype",0)
    _xdf_set_text(axis_el,"unittype",0)
    if axis_el.find("DALINK") is None:
        ET.SubElement(axis_el,"DALINK",{"index":"0"})

    if meta is not None:
        _xdf_apply_axis_meta(axis_el,meta)
    else:
        _xdf_set_math(axis_el,located.factor,located.offset)

def _xdf_z_data(axis_el, located, address:int, rows:int, cols:int, *, meta:dict|None=None):
    data=axis_el.find("EMBEDDEDDATA")
    if data is None:
        data=ET.SubElement(axis_el,"EMBEDDEDDATA")
    bits=(meta or {}).get("bits",located.width*8)
    data.set("mmedaddress",f"0x{address:X}")
    data.set("mmedelementsizebits",str(bits))
    data.set("mmedrowcount",str(rows))
    data.set("mmedcolcount",str(cols))
    data.set("mmedmajorstridebits","0")
    data.set("mmedminorstridebits","0")
    if meta is not None:
        _xdf_apply_z_meta(axis_el,meta)
    else:
        _xdf_set_math(axis_el,located.factor,located.offset)

def _xdf_constant_data(el, located, address:int, *, meta:dict|None=None):
    data=el.find("EMBEDDEDDATA")
    if data is None:
        data=ET.SubElement(el,"EMBEDDEDDATA")
    bits=(meta or {}).get("bits",located.width*8)
    data.set("mmedaddress",f"0x{address:X}")
    data.set("mmedelementsizebits",str(bits))
    if meta is not None:
        data.set("mmedtypeflags",_xdf_type_flags(meta))
        _xdf_set_text(el,"units",meta["unit"])
        precision=meta.get("precision",0)
        if precision is not None and precision >= 0:
            _xdf_set_text(el,"decimalpl",precision)
        factor=meta["factor"]; offset=meta.get("offset",0.0)
    else:
        factor=located.factor; offset=located.offset
    math_el=el.find("MATH")
    if math_el is None:
        math_el=ET.SubElement(el,"MATH")
        ET.SubElement(math_el,"VAR",{"id":"X"})
    math_el.set("equation",_xdf_math_equation(factor,offset))

def _xdf_configure_entry(el, maps, layouts, bank:int, map_name:str):
    m=maps[map_name]
    addr=_xdf_bank_addresses(maps,layouts,bank,map_name)
    meta=DR_XDF_META[map_name]
    if m.width*8 != meta["z"]["bits"]:
        raise MapSwitchError(f"Unsupported {map_name} data width for XDF")

    title=el.find("title")
    if title is not None:
        title.text=map_name if bank==1 else f"map_{bank}_{map_name}"

    desc=el.find("description")
    if desc is None:
        desc=ET.SubElement(el,"description")
    desc.text=(meta["description"] if bank==1
               else f"Multi-map set {bank}: {meta['description']}")

    if map_name in ("KFZW","KFZW2"):
        desc.text += "\nX/Y axes are shared by all banks; edits affect every bank."

    if el.tag=="XDFCONSTANT":
        _xdf_constant_data(el,m,addr["z"],meta=meta["z"])
        return

    axes={a.get("id"):a for a in el.findall("XDFAXIS")}
    if map_name in ("KFZW","KFZW2","LAMFA"):
        _xdf_axis_data(axes["x"],m.x_axis,addr["x"],meta=meta["x"])
        _xdf_axis_data(axes["y"],m.y_axis,addr["y"],meta=meta["y"])
        _xdf_z_data(axes["z"],m,addr["z"],m.y_axis.length,m.x_axis.length,meta=meta["z"])
        return

    if map_name in ("LDRXN","LDRXNZK"):
        _xdf_axis_data(axes["x"],m.y_axis,addr["x"],meta=meta["x"])
        _xdf_z_data(axes["z"],m,addr["z"],1,m.y_axis.length,meta=meta["z"])
        return

    raise MapSwitchError(f"XDF: unsupported table {map_name}")

def _xdf_clone_ldrxn_for_ldrxnzk(root, banks:int):
    """The supplied example did not include LDRXNZK; clone the matching LDRXN
    table structure so every calibration switched by the builder is editable."""
    existing={e.findtext("title") for e in root if e.find("title") is not None}
    source_by_bank={}
    for e in list(root):
        title=e.findtext("title")
        if title=="LDRXN":
            source_by_bank[1]=e
        elif title and re.fullmatch(r"map_[234]_LDRXN",title):
            source_by_bank[int(title.split("_")[1])]=e

    used={int(e.get("uniqueid"),16) for e in root if e.get("uniqueid")}
    next_id=0x6000
    insert_pos=len(root)
    for b in range(1,banks+1):
        wanted="LDRXNZK" if b==1 else f"map_{b}_LDRXNZK"
        if wanted in existing:
            continue
        src=source_by_bank.get(b)
        if src is None:
            src=source_by_bank.get(1)
        if src is None:
            raise MapSwitchError("XDF template has no LDRXN table to clone for LDRXNZK")
        clone=ET.fromstring(ET.tostring(src,encoding="unicode"))
        while next_id in used:
            next_id += 1
        clone.set("uniqueid",f"0x{next_id:X}")
        used.add(next_id)
        next_id += 1
        t=clone.find("title")
        t.text=wanted
        d=clone.find("description")
        if d is not None:
            d.text=("Maximum specified load - alternate path" if b==1
                    else f"Multi-map set {b}: Maximum specified load - alternate path")
        root.insert(insert_pos,clone)
        insert_pos += 1

def _xdf_add_experimental_tables(root, maps, layouts, banks:int):
    """Append KFZWMN/KFNWEGM tables using the supplied CSV metadata."""
    used={int(e.get("uniqueid"),16) for e in root if e.get("uniqueid")}
    uid=0x7100
    def newid():
        nonlocal uid
        while uid in used: uid+=1
        used.add(uid); x=uid; uid+=1; return f"0x{x:X}"

    for name in EXPERIMENTAL_COPY_MAPS:
        if name not in maps:
            continue
        m=maps[name]; meta=DR_XDF_META[name]
        for bank in range(1,banks+1):
            el=ET.SubElement(root,"XDFTABLE",{"uniqueid":newid(),"flags":"0x0"})
            ET.SubElement(el,"title").text=name if bank==1 else f"map_{bank}_{name}"
            ET.SubElement(el,"description").text=(meta["description"] if bank==1 else f"Multi-map set {bank}: {meta['description']}")
            ET.SubElement(el,"CATEGORYMEM",{"index":"0","category":"53" if bank>1 else "7"})
            addr=_xdf_bank_addresses(maps,layouts,bank,name)["z"]

            if name=="KFZWMN":
                # KFZWMN is 12 columns (X/load) x 16 rows (Y/RPM).
                ax=ET.SubElement(el,"XDFAXIS",{"id":"x","uniqueid":"0x0"})
                _xdf_axis_data(ax,m.x_axis,m.x_axis.address,count=12,meta=meta["x"])
                ay=ET.SubElement(el,"XDFAXIS",{"id":"y","uniqueid":"0x0"})
                _xdf_axis_data(ay,m.y_axis,m.y_axis.address,count=16,meta=meta["y"])
                z=ET.SubElement(el,"XDFAXIS",{"id":"z"})
                _xdf_z_data(z,m,addr,16,12,meta=meta["z"])
            elif name=="KFNWEGM":
                ax=ET.SubElement(el,"XDFAXIS",{"id":"x","uniqueid":"0x0"})
                _xdf_axis_data(ax,m.x_axis,m.x_axis.address,count=8,meta=meta["x"])
                ay=ET.SubElement(el,"XDFAXIS",{"id":"y","uniqueid":"0x0"})
                _xdf_axis_data(ay,m.y_axis,m.y_axis.address,count=5,meta=meta["y"])
                z=ET.SubElement(el,"XDFAXIS",{"id":"z"})
                _xdf_z_data(z,m,addr,5,8,meta=meta["z"])
            elif name=="KFTVSA":
                ax=ET.SubElement(el,"XDFAXIS",{"id":"x","uniqueid":"0x0"})
                _xdf_axis_data(ax,m.x_axis,m.x_axis.address,count=8,meta=meta["x"])
                ay=ET.SubElement(el,"XDFAXIS",{"id":"y","uniqueid":"0x0"})
                _xdf_axis_data(ay,m.y_axis,m.y_axis.address,count=5,meta=meta["y"])
                z=ET.SubElement(el,"XDFAXIS",{"id":"z"})
                _xdf_z_data(z,m,addr,5,8,meta=meta["z"])
            elif name=="KFTVSAKAT":
                ax=ET.SubElement(el,"XDFAXIS",{"id":"x","uniqueid":"0x0"})
                _xdf_axis_data(ax,m.x_axis,m.x_axis.address,count=4,meta=meta["x"])
                ay=ET.SubElement(el,"XDFAXIS",{"id":"y","uniqueid":"0x0"})
                _xdf_axis_data(ay,m.y_axis,m.y_axis.address,count=8,meta=meta["y"])
                z=ET.SubElement(el,"XDFAXIS",{"id":"z"})
                _xdf_z_data(z,m,addr,8,4,meta=meta["z"])

def generate_multimap_xdf(*,maps,layouts,banks:int,output_bin:Path) -> Path:
    """Generate a TunerPro XDF matching the exact detected/copied addresses."""
    try:
        root=ET.fromstring(EMBEDDED_XDF_TEMPLATE)
    except ET.ParseError as e:
        raise MapSwitchError(f"Embedded XDF template parse failed: {e}")

    hdr=root.find("XDFHEADER")
    if hdr is not None:
        deftitle=hdr.find("deftitle")
        if deftitle is not None:
            deftitle.text=output_bin.stem
        desc=hdr.find("description")
        if desc is not None:
            desc.text=f"Bosch ME7.5 MultiMap - {banks} banks"
        author=hdr.find("author")
        if author is not None:
            author.text="Bosch ME7.5 MultiMap Builder"

    _xdf_clone_ldrxn_for_ldrxnzk(root,banks)
    _xdf_add_experimental_tables(root,maps,layouts,banks)

    wanted_maps=("KRKTE","KFZW","KFZW2","LAMFA","LDRXN","LDRXNZK") + tuple(m for m in EXPERIMENTAL_COPY_MAPS if m in maps)
    remove=[]
    configured={(b,m) for m in EXPERIMENTAL_COPY_MAPS if m in maps for b in range(1,banks+1)}

    for el in list(root):
        title=el.findtext("title")
        if not title:
            continue

        bank=1
        base=title
        mm=re.fullmatch(r"map_(\d+)_(.+)",title)
        if mm:
            bank=int(mm.group(1))
            base=mm.group(2)

        if base not in wanted_maps:
            continue
        if base in EXPERIMENTAL_COPY_MAPS:
            continue

        if bank > banks:
            remove.append(el)
            continue

        _xdf_configure_entry(el,maps,layouts,bank,base)
        configured.add((bank,base))

    for el in remove:
        root.remove(el)

    expected={(b,m) for b in range(1,banks+1) for m in wanted_maps}
    missing=expected-configured
    if missing:
        txt=", ".join(f"Bank {b} {m}" for b,m in sorted(missing))
        raise MapSwitchError("XDF generation incomplete: "+txt)

    # RPM/Upm values are integer engine speeds. Do not display a cosmetic
    # trailing ".0" in TunerPro (e.g. 6520 instead of 6520.0).
    for axis in root.iter("XDFAXIS"):
        units=(axis.findtext("units") or "").strip().lower()
        if units in ("rpm","upm"):
            _xdf_set_text(axis,"decimalpl",0)

    # Pretty indentation while preserving a simple TunerPro-compatible XML file.
    try:
        ET.indent(root,space="  ")
    except AttributeError:
        pass

    xdf_path=output_bin.with_suffix(".xdf")
    xml_body=ET.tostring(root,encoding="unicode")
    content=f"<!-- Generated by Bosch ME7.5 MultiMap Builder -->\n{xml_body}\n"
    xdf_path.write_text(content,encoding="utf-8")

    # Verify the generated file parses back and all selected entries exist.
    check=ET.parse(xdf_path).getroot()
    titles={e.findtext("title") for e in check if e.find("title") is not None}
    for b in range(1,banks+1):
        for m in wanted_maps:
            t=m if b==1 else f"map_{b}_{m}"
            if t not in titles:
                raise MapSwitchError(f"Generated XDF verification failed: missing {t}")

    return xdf_path

def _audit_pretty_config(audit, *, p, ecu_path, entries, banks, switch_mode,
                         switch_inputs, tach_values, maps, rt, layouts,
                         selector_build, runtime_code, output_path=None, xdf_path=None):
    # SESSION
    audit.section("BUILD INFORMATION")
    audit.line(f"  Input BIN              : {p.name}", source=str(p))
    audit.line(f"  BIN size               : {p.stat().st_size} bytes")
    audit.line(f"  ECU definition         : {ecu_path.name}", source=str(ecu_path))
    audit.line(f"  ECU entries            : {len(entries)}")
    audit.line(f"  Number of banks        : {banks}")

    # SWITCH
    audit.section("MAP SWITCH CONFIGURATION")
    mode_label="Clutch + Cruise SET/RES" if switch_mode=="cruise" else "Clutch + 5x Brake"
    audit.line(f"  Switch mode            : {mode_label}")
    audit.blank()
    audit.line("  Inputs / Output")
    for x in switch_inputs:
        audit.line(f"    {x.role:<20} 0x{x.address:06X}.{x.bit}   ({x.ecu_name})",
                   name=x.role,address=f"0x{x.address:06X}.{x.bit}")

    audit.blank()
    audit.line("  Switching logic")
    if switch_mode=="cruise":
        audit.line("    Clutch + SET          : Next bank")
        audit.line("    Clutch + RES          : Previous bank")
        audit.line("    Forward cycle         : "+" -> ".join(map(str,range(1,banks+1)))+" -> 1")
        audit.line("    Reverse cycle         : "+" -> ".join(map(str,range(banks,0,-1)))+f" -> {banks}")
    else:
        audit.line("    Clutch + 5x Brake     : Next bank")
        audit.line("    Time limit            : None")
        audit.line("    Forward cycle         : "+" -> ".join(map(str,range(1,banks+1)))+" -> 1")

    audit.blank()
    audit.line("  Tachometer confirmation")
    for n,raw in enumerate(tach_values,1):
        audit.line(f"    Bank {n:<2}               : {n*1000} rpm (raw 0x{raw:04X})")
    audit.line("    After timeout          : stock nmot_w to CAN")

    # BANK LAYOUT — intentionally mirrors the clean console layout.
    audit.section("BANK LAYOUT")
    one=sum(c.size for c in layouts[0].components) if layouts else 0
    total=one*len(layouts)
    audit.line("  Stock bank              : Bank 1 (original calibration)",bank=1,status="ORIGINAL")
    audit.line(f"  Additional banks        : {len(layouts)}")
    audit.line(f"  Bytes / copied bank     : {one}")
    audit.line(f"  Total copied bytes      : {total}")

    # Bank 1 original map/component addresses.
    audit.blank()
    audit.line("  Bank 1   ORIGINAL / STOCK",bank=1,status="ORIGINAL")
    for name,kind,addr,size in _bank1_components(maps):
        audit.line(f"    {name:<18}  0x{addr:06X}                  {size:>4} B",
                   bank=1,name=name,source=f"0x{addr:06X}",destination=f"0x{addr:06X}",
                   size_bytes=size,status="ORIGINAL")

    # Copied banks, exactly in SOURCE -> DESTINATION form.
    for bl in layouts:
        audit.blank()
        audit.line(f"  Bank {bl.bank}   0x{bl.start:06X}-0x{bl.end:06X}",
                   bank=bl.bank,address=f"0x{bl.start:06X}-0x{bl.end:06X}",status="COPIED")
        for c in bl.components:
            audit.line(f"    {c.key:<18}  0x{c.source:06X} -> 0x{c.dest:06X}  {c.size:>4} B",
                       bank=bl.bank,name=c.key,source=f"0x{c.source:06X}",
                       destination=f"0x{c.dest:06X}",size_bytes=c.size,status="COPIED")

    # RUNTIME
    audit.section("RUNTIME")
    audit.line(f"  Cyclic hook             : {_hx(rt.hook)}",address=_hx(rt.hook))
    audit.line(f"  Continue at             : {_hx(getattr(rt,'continuation',None))}")
    audit.line(f"  Runtime code cave       : {_hx(rt.code_cave)}")
    audit.line(f"  Runtime RAM             : {_hx(rt.ram_base)}")
    audit.line(f"  Runtime RAM size        : 0x{getattr(rt,'ram_size',0):X} bytes" if getattr(rt,'ram_size',None) is not None else "  Runtime RAM size        : -")
    audit.line(f"  Trigger mode            : {mode_label}")
    if runtime_code is not None:
        audit.line(f"  Runtime installed       : YES ({len(runtime_code)} bytes)",status="PASS")
    else:
        audit.line("  Runtime installed       : NO",status="NOT_INSTALLED")

    # SELECTORS
    audit.section("MAP SELECTORS")
    if selector_build is None:
        audit.line("  Map selectors           : NOT INSTALLED",status="NOT_INSTALLED")
    else:
        audit.line(f"  Selector code cave      : 0x{selector_build.cave:06X}-0x{selector_build.cave+selector_build.size-1:06X}",
                   status="PASS")
        audit.line(f"  Selector hooks          : {len(selector_build.hooks)}",status="PASS")
        for h in selector_build.hooks:
            dst=selector_build.wrappers[h.wrapper_name][0]
            audit.line(f"    {h.name:<20} 0x{h.address:06X} -> 0x{dst:06X}",
                       name=h.name,source=f"0x{h.address:06X}",destination=f"0x{dst:06X}",status="PASS")

    # RESULT
    audit.section("RESULT")
    if output_path is None:
        audit.line("  BIN output              : ANALYSIS ONLY",status="ANALYSIS_ONLY")
    else:
        audit.line(f"  BIN saved               : {output_path.name}",destination=str(output_path),status="PASS")
        audit.line("  Bank 1                  : Original calibration unchanged",bank=1,status="PASS")
        if xdf_path is not None:
            audit.line(f"  XDF saved               : {xdf_path.name}",destination=str(xdf_path),status="PASS")
        audit.line("  Checksum                : Recalculate before flashing",status="REQUIRED")

def print_clean_bank_summary(layouts, one, total):
    section("BANK LAYOUT")
    print(f"  {'Stock bank':<24}: Bank 1 (original calibration)")
    print(f"  {'Additional banks':<24}: {len(layouts)}")
    print(f"  {'Bytes / copied bank':<24}: {one}")
    print(f"  {'Total copied bytes':<24}: {total}")
    for bl in layouts:
        print(f"\n  Bank {bl.bank}   0x{bl.start:06X}-0x{bl.end:06X}")
        for c in bl.components:
            print(f"    {c.key:<18}  0x{c.source:06X} -> 0x{c.dest:06X}  {c.size:>4} B")

def main():
    ap=argparse.ArgumentParser(description="ME7.5 MultiMap builder with tachometer bank confirmation via CAN nmot_w hook")
    ap.add_argument("bin_file",type=Path)
    ap.add_argument("--ecu",type=Path,help="Matching ME7Info .ecu file; default: <BIN>.ecu or the only .ecu beside BIN")
    ap.add_argument("--banks",type=int)
    ap.add_argument("--switch-mode",choices=("cruise","brake5"),help="Bank switch input method")
    ap.add_argument("--dry-run",action="store_true",help="Analysis only: do not install runtime/selectors and do not write an output BIN.")
    ap.add_argument("--write",action="store_true",help="Write output BIN (default in v0.28 unless --dry-run is used).")
    ap.add_argument("--xml-dir",type=Path,help=argparse.SUPPRESS)
    ap.add_argument("--ram-base",type=lambda x:int(x,0),help="Explicit runtime RAM base in 0x384xxx (advanced/pre-flight).")
    ap.add_argument("--accept-auto-ram",action="store_true",help="Accept RAM candidate that passes .ecu-gap + direct C167 reference scan (enabled by default for normal builds in v0.28).")
    ap.add_argument("--runtime-code-size",type=lambda x:int(x,0),default=0x300,help="Required FF code cave size for runtime, default 0x300")
    ap.add_argument("--install-bench-runtime",action="store_true",help="Install BENCH-ONLY C167 bank-counter + tachometer-confirmation runtime.")
    ap.add_argument("--install-bench-selectors",action="store_true",help="Install BENCH-ONLY map selectors. Requires --install-bench-runtime and --accept-auto-ram.")
    ap.add_argument("--tach-ticks",type=int,default=100,help="Tachometer bank-display duration in raw scheduler calls; bench-tune value, default 100")
    ap.add_argument("--csv-log",type=Path,help="CSV audit log path; default: next to BIN with automatic name")
    ap.add_argument("--verbose-analysis",action="store_true",help="Show legacy deep selector/development diagnostics (v0.16-v0.18)")
    args=ap.parse_args()

    # v0.28: simple invocation `python3 ...py file.bin` performs a complete
    # MultiMap build after the interactive bank/switch selections.
    # --dry-run remains available when analysis-only behavior is wanted.
    if not args.dry_run:
        args.write = True
        args.accept_auto_ram = True
        args.install_bench_runtime = True
        args.install_bench_selectors = True
    p=args.bin_file.expanduser().resolve()
    if not p.is_file(): raise MapSwitchError(f"BIN not found: {p}")
    original=p.read_bytes()
    if len(original)!=EXPECTED_SIZE: raise MapSwitchError(f"Expected 1048576 bytes, got {len(original)}")
    banner("ME7.5 1MB  •  MultiMap Builder  •  Automatic Build")
    ok(f"BIN loaded: {p.name}"); ok("BIN size: 1 MB")
    ecu_path=resolve_ecu_path(p,args.ecu)
    entries=parse_ecu_file(ecu_path)
    _validate_ecu_identity(original,ecu_path)
    ok(f"ECU loaded: {ecu_path.name} ({len(entries)} entries)")
    info("Searching MultiMap calibration maps...")
    maps, sources, variants=locate_maps(original, args.xml_dir)
    defs=load_definitions(args.xml_dir)
    info("Validating map addresses and ECU access points...")
    maps,sources=apply_strong_structural_pairs(original,defs,maps,sources)
    maps,sources=add_experimental_pops_maps(original,maps,sources)
    print_report(maps, sources)
    print_selector_analysis(original,maps,sources)
    missing=[x for x in REQUIRED_MAPS if x not in maps]
    missing_exp=[x for x in EXPERIMENTAL_COPY_MAPS if x not in maps]
    if missing:
        print("\nXML DIAGNOSTIC")
        print(f"  Loaded Audi XML files : {len(load_definitions(args.xml_dir))}")
        for mid in missing:
            print(f"  {mid:<8}: no supplied XML pattern/required axis matched this BIN")
        raise MapSwitchError("Required maps not found/validated by XML + paired C167 rules: "+", ".join(missing))
    if missing_exp:
        raise MapSwitchError("Runtime Pops map detection failed: "+", ".join(missing_exp))
    banks=choose_banks(args.banks)
    csv_path=(args.csv_log.expanduser().resolve() if args.csv_log else
              p.with_name(f"{p.stem}_MAPSWITCH_{banks}BANK_log.csv"))
    audit=CsvAuditLog(csv_path)

    switch_mode=choose_switch_mode(args.switch_mode)
    switch_inputs=resolve_switch_inputs(entries,switch_mode)
    tach_values=tach_raw_values(entries,banks)
    print_switch_plan(switch_mode,banks,switch_inputs,tach_values)
    data=bytearray(original)
    effective_dry_run = args.dry_run or not args.write
    layouts,one,total=allocate_and_copy(data,maps,banks,effective_dry_run)
    print_clean_bank_summary(layouts,one,total)

    if args.verbose_analysis:
        section("DEEP SELECTOR DIAGNOSTICS")
        print_v016_layout(layouts)
        print_v016_kfzw_flow(original,maps)
        print_v017_selector_patch_plan(original,maps,layouts,banks)
        print_v018_reference_strategy(original,maps)


    rt=runtime_preflight(original,entries,layouts,args.runtime_code_size,args.ram_base,args.accept_auto_ram)
    print_runtime_preflight(rt,switch_mode)

    selector_build=None
    if args.install_bench_selectors:
        if not args.install_bench_runtime:
            raise MapSwitchError("--install-bench-selectors requires --install-bench-runtime")
        selector_build=build_bench_selectors(original,maps,layouts,banks,rt)
        print_bench_selector_plan(selector_build,original)

    runtime_code=None
    if args.install_bench_runtime:
        if effective_dry_run:
            if rt.ready_for_codegen:
                byrole={x.role:x for x in switch_inputs}
                if switch_mode=="brake5":
                    runtime_code=build_bench_brake5_runtime(
                        banks=banks,ram_base=rt.ram_base,clutch=byrole["Clutch"],
                        brake=byrole["Brake"],orig_hook=rt.original_hook_bytes,tach_ticks=args.tach_ticks)
                    runtime_logic="Clutch ON + 5 Brake OFF->ON edges -> next bank"
                else:
                    runtime_code=build_bench_cruise_runtime(
                        banks=banks,ram_base=rt.ram_base,clutch=byrole["Clutch"],
                        cruise_set=byrole["Cruise SET"],cruise_res=byrole["Cruise RES"],
                        orig_hook=rt.original_hook_bytes,tach_ticks=args.tach_ticks)
                    runtime_logic="Clutch + SET -> next bank; Clutch + RES -> previous bank"
                th=find_tach_hook(original,entries)
                tc=build_tach_wrapper(banks=banks,ram_base=rt.ram_base,stock_mov=th.original,raw_values=tach_values)
                print("\nBENCH RUNTIME PLAN")
                print(f"  Runtime code     : {len(runtime_code)} B")
                print(f"  Cave             : 0x{rt.code_cave:06X}")
                print(f"  Hook CALLS       : 0x{rt.hook:06X} -> 0x{rt.code_cave:06X}")
                print(f"  RAM              : 0x{rt.ram_base:06X}")
                print(f"  Logic            : {runtime_logic}")
                print(f"  Tach duration    : {args.tach_ticks} scheduler calls (bench tune)")
                print(f"  Tach CAN hook    : 0x{th.address:06X} (stock nmot_w -> F825/F824)")
                print(f"  Tach wrapper     : {len(tc)} B")
                print(f"  Map selectors    : {'PLANNED' if selector_build is not None else 'NOT INSTALLED'}")
            else:
                print("\nBENCH RUNTIME PLAN\n  BLOCKED — runtime pre-flight must PASS.")
        else:
            runtime_code,tach_hook,tach_cave,tach_code,tach_values=install_bench_runtime(
                data,rt,banks=banks,mode=switch_mode,switch_inputs=switch_inputs,
                tach_ticks=args.tach_ticks,entries=entries,original=original)
            ok(f"BENCH C167 runtime installed ({len(runtime_code)} bytes)")
            ok(f"Scheduler hook patched: 0x{rt.hook:06X} -> 0x{rt.code_cave:06X}")
            ok(f"Tach CAN hook patched: 0x{tach_hook.address:06X} -> 0x{tach_cave:06X}")
            ok("Bank confirmation: 1=1000, 2=2000, 3=3000, 4=4000 rpm (as available banks)")
            if selector_build is not None:
                install_bench_selectors(data,original,selector_build)
                ok(f"BENCH map selectors installed ({len(selector_build.hooks)} hooks)")
                ok(f"Selector code cave: 0x{selector_build.cave:06X}-0x{selector_build.cave+selector_build.size-1:06X}")
            else:
                info("Map selectors not requested; bank state only drives tachometer confirmation.")

    if effective_dry_run:
        _audit_pretty_config(
            audit,p=p,ecu_path=ecu_path,entries=entries,banks=banks,
            switch_mode=switch_mode,switch_inputs=switch_inputs,tach_values=tach_values,
            maps=maps,rt=rt,layouts=layouts,selector_build=selector_build,
            runtime_code=runtime_code,output_path=None,xdf_path=None)
        audit.write()
        section("RESULT")
        info("Analysis only: no BIN output written.")
        ok(f"CSV log saved: {audit.path}")
        return 0
    out=(bench_full_output_name(p,banks) if args.install_bench_selectors else (bench_output_name(p,banks) if args.install_bench_runtime else output_name(p,banks))); out.write_bytes(data)
    if out.read_bytes()!=bytes(data): raise MapSwitchError("Output verification failed")
    xdf_path=generate_multimap_xdf(maps=maps,layouts=layouts,banks=banks,output_bin=out)
    _audit_pretty_config(
        audit,p=p,ecu_path=ecu_path,entries=entries,banks=banks,
        switch_mode=switch_mode,switch_inputs=switch_inputs,tach_values=tach_values,
        maps=maps,rt=rt,layouts=layouts,selector_build=selector_build,
        runtime_code=runtime_code,output_path=out,xdf_path=xdf_path)
    audit.write()
    section("RESULT")
    ok(f"BIN saved: {out}")
    ok(f"XDF saved: {xdf_path}")
    ok("Bank 1 remained untouched; only N-1 calibration copies were created")
    ok(f"CSV log saved: {audit.path}")
    if args.install_bench_selectors:
        print("[BENCH] Runtime + existing map selectors installed. Bank 1 is stock; added banks use copied calibrations.")
        print("[BENCH] KFZWMN/KFNWEGM/KFTVSA/KFTVSAKAT runtime selectors installed with validated dynamic hook discovery.")
        print("[BENCH] Validate on bench before any vehicle use. Recalculate ME7 checksum before flashing.")
    elif args.install_bench_runtime:
        print("[BENCH] Runtime changes bank counter + tachometer confirmation only. Calibration lookups still use stock Bank 1.")
    return 0

def _emit_checked_bank(a: _C167Builder, reg: int, ram_base: int):
    """Uninitialized state selects Bank 1, without writing RAM in a selector."""
    prefix = f"state_{len(a.b)}"
    _emit_mov_reg_mem(a, reg, ram_base + 12)
    a.emit(0x46, reg, _le16(0x4D53))
    a.jmpr(CC_Z, prefix + "_valid")
    _emit_mov_reg_imm(a, reg, 1)
    a.jmpr(CC_UC, prefix + "_done")
    a.label(prefix + "_valid")
    _emit_mov_reg_mem(a, reg, ram_base)
    a.label(prefix + "_done")

def _ecu_ram_spans(entries):
    spans = []
    for entry in entries.values():
        try:
            addr = int(entry.columns[1], 16)
            size = int(entry.columns[2], 0)
        except (IndexError, ValueError):
            continue
        if 0x380000 <= addr < 0x390000 and size > 0:
            spans.append((addr, addr + size))
    return sorted(spans)

def _validate_ecu_identity(binary: bytes, ecu_path: Path):
    text = ecu_path.read_text(encoding='latin-1')
    for key in ('HWNumber', 'SWNumber', 'PartNumber'):
        m = re.search(r'^\s*' + key + r'\s*=\s*\{([^}]+)\}', text, re.M)
        if not m or m.group(1).strip().encode('ascii') not in binary:
            raise MapSwitchError(f'.ecu identity mismatch or missing {key}')

def _verified_ignition_groups(binary: bytes, anchor: int):
    pattern = re.compile(rb'\xE6\xFC(..)\xE6\xFD(..)\xF2\xFE(..)\xF2\xFF(..)\xDA(.)(..)', re.S)
    lo, hi = max(0,anchor-192), min(len(binary),anchor+224)
    return [m for m in pattern.finditer(binary,lo,hi) if not m.start() & 1]

def _validated_selector_references(binary, maps):
    krefs = {'KFZW':[], 'KFZW2':[]}
    groups = _verified_ignition_groups(binary,maps['KFZW'].pattern_location)
    near_signature = bytes.fromhex('a9 4d c0 4d f0 1e c0 31 1b 1d f2 f1 0e fe 00 c1 f0 1f c0 31 00 c1 a9 4c d0 42 5c 8f 7c 1f 2d 09 f4 6c 01 00 d0 63 20 32 5c 13 0b 3f f2 f3 0c fe 00 23 f0 42 5c 8e 7c 1e 2d 13 00 cd 99 4c d0 42 78 f0 2d 08 a9 6c d0 63 20 32 5c 13 0b 3f f2 f3 0c fe 00 23 20 24 5c 12 0b 2e f2 f2 0c fe 00 42 db 00')
    for m in groups:
        target = _linear_from_dpp_word(int.from_bytes(m.group(1),'little'))
        name = next((n for n in krefs if maps[n].address==target),None)
        if name is None:
            raise MapSwitchError('Unexpected ignition lookup in paired code block')
        if _linear_from_dpp_word(int.from_bytes(m.group(2),'little')) != maps[name].x_axis.address-2:
            raise MapSwitchError('Ignition stride header disagrees with executable axis reference')
        pos = m.start()+16
        raw,dst = _raw_calls_target(binary,pos)
        if binary[dst:dst+len(near_signature)]!=near_signature:
            raise MapSwitchError('Unsupported Bosch near ignition lookup implementation')
        krefs[name].append((pos,raw,dst))
    if any(len(v)!=2 for v in krefs.values()):
        raise MapSwitchError('Expected four complete ignition pointer/input/CALLS groups')
    raw_calls={entry[1] for group in krefs.values() for entry in group}
    if len(raw_calls)!=1:
        raise MapSwitchError('Ignition lookup targets differ')
    ignition_call=next(iter(raw_calls))

    la=maps['LDRXNZK'].pattern_location
    # Structural pair finder anchors exactly at the first complete setup.
    l_calls=[]
    for name,at in [('LDRXNZK',la),('LDRXN',la+20)]:
        block=binary[at:at+16]
        if len(block)!=16 or block[:2]!=b'\xE6\xFC' or block[4:6]!=b'\xE6\xFD' or block[8:10]!=b'\xF2\xFE':
            raise MapSwitchError('Incomplete LDR setup')
        pointer=int.from_bytes(block[2:4],'little')+int.from_bytes(block[6:8],'little')*0x4000-0x800000
        if pointer!=maps[name].y_axis.address-2:
            raise MapSwitchError('LDR pointer mismatch')
        raw,dst=_raw_calls_target(binary,at+12)
        l_calls.append((at+12,raw,dst))
    if l_calls[0][1]!=l_calls[1][1]:
        raise MapSwitchError('LDR lookup targets differ')

    lam=maps['LAMFA'];at=lam.pattern_location-2
    # Each opcode and stack operation in the complete LAMFA setup is checked.
    md,mo=_flash_operand(lam.address)[1:]
    xd,xo=_flash_operand(lam.x_axis.address)[1:]
    hd,ho=_flash_operand(lam.y_axis.address-2)[1:]
    yd,yo=_flash_operand(lam.y_axis.address)[1:]
    expected=(b'\xE6\xF4'+_le16(mo)+b'\xE6\xF5'+_le16(md)+b'\x88\x50\x88\x40'+
              b'\xE6\xF4'+_le16(xo)+b'\xE6\xF5'+_le16(xd)+b'\x88\x50\x88\x40'+
              b'\xD7\x40'+_le16(hd)+b'\xC2\xFC'+_le16(ho)+
              b'\xE6\xFD'+_le16(yo)+b'\xE6\xFE'+_le16(yd)+
              b'\xD7\x40'+_le16(hd)+b'\xC2\xFF'+_le16(ho+1))
    if binary[at:at+len(expected)]!=expected:
        raise MapSwitchError('Unsupported LAMFA pointer/header/stack setup')
    pos=at+len(expected);raw,dst=_raw_calls_target(binary,pos)
    lamfa_call=(pos,raw,dst)

    kr=maps['KRKTE']
    if kr.width!=2:
        raise MapSwitchError('8-bit KRKTE selector requires a separate verified implementation')
    _,page,operand=_flash_operand(kr.address)
    pat=re.compile(re.escape(b'\xD7\x40'+_le16(page))+rb'\xF2([\xF2\xF4\xF5])'+re.escape(_le16(operand)))
    kr_reads=[]
    for match in pat.finditer(binary):
        if match.start() & 1:
            continue
        p=match.start()+4
        kr_reads.append((p,bytes(binary[p:p+4]),page,operand))
    if len(kr_reads)!=3 or {x[1][1] for x in kr_reads}!={0xF2,0xF4,0xF5}:
        raise MapSwitchError('Expected three page-qualified KRKTE reads in R2/R4/R5')
    return krefs,ignition_call,l_calls,lamfa_call,kr_reads

def _pops_axis_payload(binary: bytes, address: int, count: int):
    if not 0<=address<address+count<=len(binary):
        raise MapSwitchError('Pops axis is outside the BIN')
    values=list(binary[address:address+count])
    if len(set(values))<2 or any(a>b for a,b in zip(values,values[1:])):
        raise MapSwitchError('Pops axis is not an increasing BYTE axis')
    return address

def _pops_row_axis(binary: bytes, setup: int, count: int):
    """Follow the last pushed packed input to its page-qualified axis producer.

    The unsigned far lookup takes Y and X from [R0]/[R0+2]. R14/R15
    hold the column-count pointer, not the packed interpolation inputs.
    """
    load=binary[setup-6:setup]
    if (len(load)!=6 or load[0]!=0xF2 or load[1]<0xF0 or
            load[4:]!=bytes((0x88,(load[1]&15)<<4))):
        raise MapSwitchError('Pops lookup: unsupported software-stack input setup')
    prefix=binary[setup-10:setup-6]
    word=int.from_bytes(load[2:4],'little')
    if prefix[:2]==b'\xD7\x40':
        page=int.from_bytes(prefix[2:],'little')
    else:
        page=(0x204,0x205,0xE0,3)[word>>14]
    ram=(page<<14)+(word&0x3FFF)
    # Exact instruction families, including the complete result store.
    rx=re.compile(
        rb'\xE6\xFC(?P<axis>..)\xE6\xFD(?P<page>..)'
        rb'(?:\xD7\x40..)?\xC2\xFE..'
        rb'(?:\xD7\x40..\xF2\xFF..)?\xDA...'
        rb'\xD7\x40(?P<ram_page>..)\xF6\xF4(?P<result>..)',re.S)
    axes=set()
    for match in rx.finditer(binary):
        if match.start()&1:
            continue
        store=(int.from_bytes(match['ram_page'],'little')<<14)+(int.from_bytes(match['result'],'little')&0x3FFF)
        if store!=ram:
            continue
        header=(int.from_bytes(match['page'],'little')<<14)+(int.from_bytes(match['axis'],'little')&0x3FFF)-0x800000
        if not 0<=header<header+count+1<=len(binary) or binary[header]!=count:
            raise MapSwitchError('Pops axis producer disagrees with the table geometry')
        axes.add(_pops_axis_payload(binary,header+1,count))
    if len(axes)!=1:
        raise MapSwitchError(f'Pops row axis: expected one resolved axis, got {len(axes)}')
    return axes.pop()

def _find_unreserved_ff(data,size,start,end,alignment,reserved):
    while start+size<=end:
        pos=find_ff_hole(data,size,start,end,alignment)
        if pos is None:
            return None
        overlaps=[b for a,b in reserved if ranges_overlap(pos,pos+size,a,b)]
        if not overlaps:
            return pos
        start=align_up(max(overlaps),alignment)
    return None

if __name__=="__main__":
    try: raise SystemExit(main())
    except MapSwitchError as e:
        print(f"{_tag('[ERROR]', _RED)} {e}",file=sys.stderr); raise SystemExit(2)
    except KeyboardInterrupt:
        print(f"\n{_tag('[ERROR]', _RED)} Cancelled",file=sys.stderr); raise SystemExit(130)


# ---------------------------------------------------------------------------
# v0.13 SELECTOR ANALYSIS NOTES
# ---------------------------------------------------------------------------
# v0.38 has software validation; this does not replace testing on an ECU.
# Do NOT redirect calibration lookups merely from the map addresses.
# Before selector patching, uniquely validate executable lookup/read sites for:
#   LAMFA, KFZW, KFZW2, LDRXN, LDRXNZK, KRKTE
# Save original instruction bytes at every site and abort on ambiguity.
# KFZW/KFZW2 and LDRXN/LDRXNZK already have paired-C167 structural anchors
# from the finder; LAMFA and KRKTE still need executable-site validation.
