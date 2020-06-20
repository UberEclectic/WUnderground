#!/usr/bin/env python2.6
# -*- coding: utf-8 -*-

"""
WUnderground Plugin
plugin.py
Author: DaveL17
Credits:
Update Checker by: berkinet (with additional features by Travis Cook)

The WUnderground plugin downloads JSON data from Weather Underground and parses
it into custom device states. Theoretically, the user can create an unlimited
number of devices representing individual observation locations. The
WUnderground plugin will update each custom device found in the device
dictionary incrementally. The user can have independent settings for each
weather location.

The base Weather Underground developer plan allows for 10 calls per minute and
a total of 500 per day. Setting the plugin for 5 minute refreshes results in
288 calls per device per day. In other words, two devices (with different
location settings) at 5 minutes will be an overage. The plugin makes only one
call per location per cycle. See Weather Underground for more information on
API call limitations.

The plugin tries to leave WU data unchanged. But in order to be useful, some
changes need to be made. The plugin adjusts the raw JSON data in the following
ways:
- The barometric pressure symbol is changed to something more human
  friendly: (+ -> ^, 0 -> -, - -> v).
- Takes numerics and converts them to strings for Indigo compatibility
  where necessary.
- Strips non-numeric values from numeric values for device states where
  appropriate (but retains them for ui.Value)
- Weather Underground is inconsistent in the data it provides as
  strings and numerics. Sometimes a numeric value is provided as a
  string and we convert it to a float where useful.
- Sometimes, WU provides a value that would break Indigo logic.
  Conversions made:
 - Replaces anything that is not a rational value (i.e., "--" with "0"
   for precipitation since precipitation can only be zero or a
   positive value) and replaces "-999.0" with a value of -99.0 and a UI value
   of "--" since the actual value could be positive or negative.

 Not all values are available in all API calls.  The plugin makes these units
 available:
 distance       w    -    -    -
 percentage     w    t    h    -
 pressure       w    -    -    -
 rainfall       w    t    h    -
 snow           -    t    h    -
 temperature    w    t    h    a
 wind           w    t    h    -
 (above: _w_eather, _t_en day, _h_ourly, _a_lmanac)

Weather data copyright Weather Underground and Weather Channel, LLC., (and its
subsidiaries), or respective data providers. This plugin and its author are in
no way affiliated with Weather Underground, LLC. For more information about
data provided see Weather Underground Terms of Service located at:
http://www.wunderground.com/weather/api/d/terms.html.

For information regarding the use of this plugin, see the license located in
the plugin package or located on GitHub:
https://github.com/DaveL17/WUnderground/blob/master/LICENSE
"""

# =================================== TO DO ===================================

# TODO: None

# ================================== IMPORTS ==================================

# Built-in modules
import datetime as dt
import pytz
import simplejson
import socket
import sys
import time

try:
    import requests
except ImportError:
    import urllib
    import urllib2

# Third-party modules
from DLFramework import indigoPluginUpdateChecker
try:
    import indigo
except ImportError:
    pass

try:
    import pydevd
except ImportError:
    pass

# My modules
import DLFramework.DLFramework as Dave

# =================================== HEADER ==================================

__author__    = Dave.__author__
__copyright__ = Dave.__copyright__
__license__   = Dave.__license__
__build__     = Dave.__build__
__title__ = "WUnderground Plugin for Indigo Home Control"
__version__ = "7.0.00"

# =============================================================================

kDefaultPluginPrefs = {
    u'alertLogging': False,           # Write severe weather alerts to the log?
    u'apiKey': "",                    # WU requires the api key.
    u'callCounter': 500,              # WU call limit based on UW plan.
    u'dailyCallCounter': 0,           # Number of API calls today.
    u'dailyCallDay': '1970-01-01',    # API call counter date.
    u'dailyCallLimitReached': False,  # Has the daily call limit been reached?
    u'downloadInterval': 900,         # Frequency of weather updates.
    u'itemListTempDecimal': 1,        # Precision for Indigo Item List.
    u'language': "EN",                # Language for WU text.
    u'noAlertLogging': False,         # Suppresses "no active alerts" logging.
    u'showDebugInfo': False,          # Verbose debug logging?
    u'showDebugLevel': 1,             # Low, Medium or High debug output.
    u'uiDateFormat': u"DD-MM-YYYY",   # Preferred date format string.
    u'uiHumidityDecimal': 1,          # Precision for Indigo UI display (humidity).
    u'uiTempDecimal': 1,              # Precision for Indigo UI display (temperature).
    u'uiTimeFormat': u"military",     # Preferred time format string.
    u'uiWindDecimal': 1,              # Precision for Indigo UI display (wind).
    u'updaterEmail': "",              # Email to notify of plugin updates.
    u'updaterEmailsEnabled': False    # Notification of plugin updates wanted.
}

pad_log = u"{0}{1}".format('\n', " " * 34)  # 34 spaces to align with log margin.


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        self.debug = self.pluginPrefs.get('showDebugInfo', True)
        self.updater = indigoPluginUpdateChecker.updateChecker(self, "https://raw.githubusercontent.com/DaveL17/WUnderground/master/wunderground_version.html")

        self.masterWeatherDict = {}
        self.masterTriggerDict = {}
        self.wuOnline = True

        # ====================== Initialize DLFramework =======================

        self.Fogbert   = Dave.Fogbert(self)
        self.Formatter = Dave.Formatter(self)

        self.date_format = self.Formatter.dateFormat()
        self.time_format = self.Formatter.timeFormat()

        # Log pluginEnvironment information when plugin is first started
        self.Fogbert.pluginEnvironment()

        # Convert old debugLevel scale (low, medium, high) to new scale (1, 2, 3).
        if not 0 < self.pluginPrefs.get('showDebugLevel', 1) <= 3:
            self.pluginPrefs['showDebugLevel'] = self.Fogbert.convertDebugLevel(self.pluginPrefs['showDebugLevel'])

        # =====================================================================

        # If debug is turned on and set to high, warn the user of potential risks.
        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"{0}{1}Caution! Debug set to high. Output contains sensitive information (API key, location, email, etc.{1}{0})".format('=' * 98, pad_log))

            self.sleep(3)
            self.debugLog(u"============ pluginPrefs ============")
            for key, value in pluginPrefs.iteritems():
                self.debugLog(u"{0}: {1}".format(key, value))
        else:
            self.debugLog(u"Plugin preference logging is suppressed. Set debug level to [High] to write them to the log.")

        # try:
        #     pydevd.settrace('localhost', port=5678, stdoutToServer=True, stderrToServer=True, suspend=False)
        # except:
        #     pass

    def __del__(self):
        indigo.PluginBase.__del__(self)

    def actionRefreshWeather(self, valuesDict):
        """ The actionRefreshWeather() method calls the refreshWeatherData()
        method to request a complete refresh of all weather data (Actions.XML
        call.) """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"actionRefreshWeather called.")
            self.debugLog(u"valuesDict: {0}".format(valuesDict))

        self.refreshWeatherData()

    def callCount(self):
        """ Maintains a count of daily calls to Weather Underground to help
        ensure that the plugin doesn't go over a user-defined limit. The limit
        is set within the plugin config dialog. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"callCount() method called.")

        calls_made = self.pluginPrefs['dailyCallCounter']  # Calls today so far
        calls_max = self.pluginPrefs.get('callCounter', 500)  # Max calls allowed per day
        download_interval = self.pluginPrefs.get('downloadInterval', 15)

        # See if we have exceeded the daily call limit.  If we have, set the "dailyCallLimitReached" flag to be true.
        if calls_made >= calls_max:
            indigo.server.log(u"Daily call limit ({0}) reached. Taking the rest of the day off.".format(calls_max), type="WUnderground Status")
            self.debugLog(u"  Setting call limiter to: True")

            self.pluginPrefs['dailyCallLimitReached'] = True

            self.sleep(download_interval)

        # Daily call limit has not been reached. Increment the call counter (and ensure that call limit flag is set to False.
        else:
            # Increment call counter and write it out to the preferences dict.
            self.pluginPrefs['dailyCallLimitReached'] = False
            self.pluginPrefs['dailyCallCounter'] += 1

            # Calculate how many calls are left for debugging purposes.
            calls_left = calls_max - calls_made
            self.debugLog(u"  {0} callsLeft = ({1} - {2})".format(calls_left, calls_max, calls_made))

    def callDay(self):
        """ Manages the day for the purposes of maintaining the call counter
        and the flag for the daily forecast email message. """

        wu_time_zone       = pytz.timezone('US/Pacific-New')
        call_day           = self.pluginPrefs['dailyCallDay']
        call_limit_reached = self.pluginPrefs.get('dailyCallLimitReached', False)
        debug_level        = self.pluginPrefs.get('showDebugLevel', 1)
        sleep_time         = self.pluginPrefs.get('downloadInterval', 15)
        # todays_date        = dt.datetime.today().date()  # this was the old method, to compare with local server's date
        todays_date        = dt.datetime.now(wu_time_zone).date()  # this is the new method, to compare with the WU server's date
        today_str          = u"{0}".format(todays_date)
        today_unstr        = dt.datetime.strptime(call_day, "%Y-%m-%d")
        today_unstr_conv   = today_unstr.date()

        if debug_level >= 3:
            self.debugLog(u"callDay() method called.")

        if debug_level >= 2:
            self.debugLog(u"  callDay: {0}".format(call_day))
            self.debugLog(u"  dailyCallLimitReached: {0}".format(call_limit_reached))
            self.debugLog(u"  Is todays_date: {0} greater than dailyCallDay: {1}?".format(todays_date, today_unstr_conv))

        # Check if callDay is a default value and set to today if it is.
        if call_day in ["", "2000-01-01"]:
            self.debugLog(u"  Initializing variable dailyCallDay: {0}".format(today_str))

            self.pluginPrefs['dailyCallDay'] = today_str

        # Reset call counter and call day because it's a new day.
        if todays_date > today_unstr_conv:
            self.pluginPrefs['dailyCallCounter'] = 0
            self.pluginPrefs['dailyCallLimitReached'] = False
            self.pluginPrefs['dailyCallDay'] = today_str

            # If it's a new day, reset the forecast email sent flags.
            for dev in indigo.devices.itervalues('self'):
                try:
                    if 'weatherSummaryEmailSent' in dev.states:
                        dev.updateStateOnServer('weatherSummaryEmailSent', value=False)

                except Exception as error:
                    self.debugLog(u"Exception updating weather summary email sent value. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

            if debug_level >= 2:
                self.debugLog(u"  Today is a new day. Reset the call counter.\n"
                              u"  Reset dailyCallLimitReached to: False\n"
                              u"  Reset dailyCallCounter to: 0\n"
                              u"  Update dailyCallDay to: {0}".format(today_str))
            self.updater.checkVersionPoll()

        else:
            if debug_level >= 2:
                self.debugLog(u"    Today is not a new day.")

        if call_limit_reached:
            indigo.server.log(u"    Daily call limit reached. Taking the rest of the day off.", type="WUnderground Status")
            self.sleep(sleep_time)

        else:
            if debug_level >= 2:
                self.debugLog(u"    The daily call limit has not been reached.")

    def checkVersionNow(self):
        """ The checkVersionNow() method will call the Indigo Plugin Update
        Checker based on a user request. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"checkVersionNow() method called.")

        try:
            self.updater.checkVersionNow()

        except Exception as error:
            self.errorLog(u"Error checking plugin update status. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            # return False

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        """ User closes config menu. The validatePrefsConfigUI() method will
        also be called. """

        debug_level = valuesDict['showDebugLevel']
        show_debug = valuesDict['showDebugInfo']

        if debug_level >= 3:
            self.debugLog(u"closedPrefsConfigUi() method called.")

        if userCancelled:
            self.debugLog(u"  User prefs dialog cancelled.")

        if not userCancelled:
            self.debug = show_debug

            # Debug output can contain sensitive data.
            if debug_level >= 3:
                self.debugLog(u"============ valuesDict ============")
                for key, value in valuesDict.iteritems():
                    self.debugLog(u"{0}: {1}".format(key, value))
            else:
                self.debugLog(u"Plugin preferences suppressed. Set debug level to [High] to write them to the log.")

            if self.debug:
                self.debugLog(u"  Debugging on.{0}Debug level set to [Low (1), Medium (2), High (3)]: {1}".format(pad_log, show_debug))
            else:
                self.debugLog(u"Debugging off.")

            self.debugLog(u"User prefs saved.")

    def commsKillAll(self):
        """ commsKillAll() sets the enabled status of all plugin devices to
        false. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"commsKillAll method() called.")

        for dev in indigo.devices.itervalues("self"):
            try:
                indigo.device.enable(dev, value=False)

            except Exception as error:
                self.debugLog(u"Exception when trying to kill all comms. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

    def commsUnkillAll(self):
        """ commsUnkillAll() sets the enabled status of all plugin devices to
        true. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"commsUnkillAll method() called.")

        for dev in indigo.devices.itervalues("self"):
            try:
                indigo.device.enable(dev, value=True)

            except Exception as error:
                self.debugLog(u"Exception when trying to unkill all comms. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

    def debugToggle(self):
        """ Toggle debug on/off. """

        debug_level = self.pluginPrefs['showDebugLevel']

        if not self.debug:
            self.pluginPrefs['showDebugInfo'] = True
            self.debug = True
            self.debugLog(u"Debugging on. Debug level set to [Low (1), Medium (2), High (3)]: {0}".format(debug_level))

            # Debug output can contain sensitive info, show only if debug level is high.
            if debug_level >= 3:
                self.debugLog(u"{0}{1}Caution! Debug set to high. Output contains sensitive information (API key, location, email, etc.{1}{0}".format('=' * 98, pad_log))
            else:
                self.debugLog(u"Plugin preferences suppressed. Set debug level to [High] to write them to the log.")
        else:
            self.pluginPrefs['showDebugInfo'] = False
            self.debug = False
            indigo.server.log(u"Debugging off.", type="WUnderground Status")

    def deviceStartComm(self, dev):
        """ Start communication with plugin devices. """

        self.debugLog(u"Starting Device: {0}".format(dev.name))

        dev.stateListOrDisplayStateIdChanged()  # Check to see if the device profile has changed.

        # For devices that display the temperature as their UI state, set them to a value we already have.
        try:
            if dev.model in ['WUnderground Device', 'WUnderground Weather', 'WUnderground Weather Device', 'Weather Underground', 'Weather']:
                dev.updateStateOnServer('onOffState', value=True, uiValue=u"{0}{1}".format(dev.states['temp'], dev.pluginProps.get('temperatureUnits', '')))

            else:
                dev.updateStateOnServer('onOffState', value=True, uiValue=u"Enabled")

        except Exception as error:
            self.debugLog(u"Error setting deviceUI temperature field. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            self.debugLog(u"No existing data to use. UI temp will be updated momentarily.")

        # Set all device icons to off.
        for attr in ['SensorOff', 'TemperatureSensorOff']:
            try:
                dev.updateStateImageOnServer(getattr(indigo.kStateImageSel, attr))
            except AttributeError:
                pass

    def deviceStopComm(self, dev):
        """ Stop communication with plugin devices. """

        self.debugLog(u"Stopping Device: {0}".format(dev.name))

        try:
            dev.updateStateOnServer('onOffState', value=False, uiValue=u"Disabled")
        except Exception as error:
            self.debugLog(u"deviceStopComm error. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        # Set all device icons to off.
        for attr in ['SensorOff', 'TemperatureSensorOff']:
            try:
                dev.updateStateImageOnServer(getattr(indigo.kStateImageSel, attr))
            except AttributeError:
                pass

    def dumpTheJSON(self):
        """ The dumpTheJSON() method reaches out to Weather Underground, grabs
        a copy of the configured JSON data and saves it out to a file placed in
        the Indigo Logs folder. If a weather data log exists for that day, it
        will be replaced. With a new day, a new log file will be created (file
        name contains the date.) """

        file_name = '{0}/{1} Wunderground.txt'.format(indigo.server.getLogsFolderPath(), dt.datetime.today().date())

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"dumpTheJSON() method called.")

        try:

            with open(file_name, 'w') as logfile:

                # This works, but PyCharm doesn't like it as Unicode.  Encoding clears the inspection error.
                logfile.write(u"Weather Underground JSON Data\n".encode('utf-8'))
                logfile.write(u"Written at: {0}\n".format(dt.datetime.today().strftime('%Y-%m-%d %H:%M')).encode('utf-8'))
                logfile.write(u"{0}{1}".format("=" * 72, '\n').encode('utf-8'))

                for key in self.masterWeatherDict.keys():
                    logfile.write(u"Location Specified: {0}\n".format(key).encode('utf-8'))
                    logfile.write(u"{0}\n\n".format(self.masterWeatherDict[key]).encode('utf-8'))

            indigo.server.log(u"Weather data written to: {0}".format(file_name), type="WUnderground Status")

        except IOError:
            indigo.server.log(u"Unable to write to Indigo Log folder.", type="WUnderground Status", isError=True)

    def dumpTheDict(self):
        """ The dumpTheDict() method pretty-prints the Dictionary """

        file_name = '{0}/{1} Wunderground_Dictionary.txt'.format(indigo.server.getLogsFolderPath(), dt.datetime.today().date())

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"dumpTheDict() method called.")

        try:

            with open(file_name, 'w') as logfile:

                # This works, but PyCharm doesn't like it as Unicode.  Encoding clears the inspection error.
                logfile.write(u"Weather Underground Dictionary\n".encode('utf-8'))
                logfile.write(u"Written at: {0}\n".format(dt.datetime.today().strftime('%Y-%m-%d %H:%M')).encode('utf-8'))
                logfile.write(u"{0}{1}".format("=" * 72, '\n').encode('utf-8'))

	        logfile.write(u"{0}\n".format(self.masterWeatherDict).encode('utf-8'))

            indigo.server.log(u"Weather dictionary written to: {0}".format(file_name), type="WUnderground Dictionary")

        except IOError:
            indigo.server.log(u"Unable to write to Indigo Log folder.", type="WUnderground Dictionary", isError=True)

    def fixCorruptedData(self, state_name, val):
        """ Sometimes WU receives corrupted data from personal weather
        stations. Could be zero, positive value or "--" or "-999.0" or
        "-9999.0". This method tries to "fix" these values for proper display.
        Since there's no possibility of negative precipitation, we convert that
        to zero. Even though we know that -999 is not the same as zero, it's
        functionally the same. Thanks to "jheddings" for the better
        implementation of this method. """

        try:
            val = float(val)

            if val < -55.728:  # -99 F = -55.728 C. No logical value less than -55.7 should be possible.
                self.debugLog(u"Fixed corrupted data {0}: {1}. Returning: {2}, {3}".format(state_name, val, -99.0, u"--"))
                return -99.0, u"--"

            else:
                return val, str(val)

#        except (ValueError, TypeError):
        except (ValueError):
            self.debugLog(u"Fixed corrupted data {0}. Returning: {1}, {2}".format(val,-99.0, u"--"))
            return -99.0, u"--"

    def fixPressureSymbol(self, state_name, val):
        """ Converts the barometric pressure symbol to something more human
        friendly. """

        try:
            if val == "+":
                return u"^"
            elif val == "-":
                return u"v"
            elif val == "0":
                return u"-"

            else:
                return u"?"
        except Exception as error:
            self.debugLog(u"Exception in fixPressureSymbol. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            return val

    def floatEverything(self, state_name, val):
        """ This doesn't actually float everything. Select values are sent here
        to see if they float. If they do, a float is returned. Otherwise, a
        Unicode string is returned. This is necessary because Weather
        Underground will send values that won't float even when they're
        supposed to. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"floatEverything(self, state_name={0}, val={1})".format(state_name, val))

        try:
            return float(val)

        except (ValueError, TypeError) as error:
            self.debugLog(u"Line {0}  {1}) (val = {2})".format(sys.exc_traceback.tb_lineno, error, val))
            return -99.0

    def getDeviceConfigUiValues(self, valuesDict, typeId, devId):
        """Called when a device configuration dialog is opened. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"getDeviceConfigUiValues() called.")

        return valuesDict

    def getLatLong(self, valuesDict, typeId, devId):
        """Called when a device configuration dialog is opened. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"getDeviceConfigUiValues() called.")

        latitude, longitude = indigo.server.getLatitudeAndLongitude()
        valuesDict['centerlat'] = latitude
        valuesDict['centerlon'] = longitude

        return valuesDict

    def getSatelliteImage(self, dev):
        """ The getSatelliteImage() method will download a file from a user-
        specified location and save it to a user-specified folder on the local
        server. This method is used by the Satellite Image Downloader device 
        type. """

        debug_level = self.pluginPrefs['showDebugLevel']
        destination = dev.pluginProps['imageDestinationLocation']
        source      = dev.pluginProps['imageSourceLocation']

        if debug_level >= 3:
            self.debugLog(u"getSatelliteImage() method called.")

        try:
            if destination.endswith((".gif", ".jpg", ".jpeg", ".png")):

                # If requests doesn't work for some reason, revert to urllib.
                try:
                    r = requests.get(source, stream=True, timeout=10)

                    with open(destination, 'wb') as img:
                        for chunk in r.iter_content(2000):
                            img.write(chunk)

                except NameError:
                    urllib.urlretrieve(source, destination)

                dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")
                dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)

                if debug_level >= 2:
                    self.debugLog(u"Image downloader source: {0}".format(source))
                    self.debugLog(u"Image downloader destination: {0}".format(destination))
                    self.debugLog(u"Satellite image downloaded successfully.")

                return

            else:
                self.errorLog(u"The image destination must include one of the approved types (.gif, .jpg, .jpeg, .png)")
                dev.updateStateOnServer('onOffState', value=False, uiValue=u"Bad Type")
                return False

        except Exception as error:
            self.errorLog(u"Error downloading satellite image. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u"No comm")

    def getWUradar(self, dev):
        """ The getWUradar() method will download a satellite image from 
        Weather Underground. The construction of the image is based upon user
        preferences defined in the WUnderground Radar device type. """

        debug_level = self.pluginPrefs['showDebugLevel']
        location    = ''
        name        = dev.pluginProps['imagename']
        parms       = ''
        parms_dict = {
            'apiref': '97986dc4c4b7e764',
            'centerlat': float(dev.pluginProps.get('centerlat', 41.25)),
            'centerlon': float(dev.pluginProps.get('centerlon', -87.65)),
            'delay': int(dev.pluginProps.get('delay', 25)),
            'feature': dev.pluginProps.get('feature', True),
            'height': int(dev.pluginProps.get('height', 500)),
            'imagetype': dev.pluginProps.get('imagetype', 'radius'),
            'maxlat': float(dev.pluginProps.get('maxlat', 43.0)),
            'maxlon': float(dev.pluginProps.get('maxlon', -90.5)),
            'minlat': float(dev.pluginProps.get('minlat', 39.0)),
            'minlon': float(dev.pluginProps.get('minlon', -86.5)),
            'newmaps': dev.pluginProps.get('newmaps', False),
            'noclutter': dev.pluginProps.get('noclutter', True),
            'num': int(dev.pluginProps.get('num', 10)),
            'radius': float(dev.pluginProps.get('radius', 150)),
            'radunits': dev.pluginProps.get('radunits', 'nm'),
            'rainsnow': dev.pluginProps.get('rainsnow', True),
            'reproj.automerc': dev.pluginProps.get('Mercator', False),
            'smooth': dev.pluginProps.get('smooth', 1),
            'timelabel.x': int(dev.pluginProps.get('timelabelx', 10)),
            'timelabel.y': int(dev.pluginProps.get('timelabely', 20)),
            'timelabel': dev.pluginProps.get('timelabel', True),
            'width': int(dev.pluginProps.get('width', 500)),
        }

        if debug_level >= 3:
            self.debugLog(u"getSatelliteImage() method called.")

        try:

            # Type of image
            if parms_dict['feature']:
                radartype = 'animatedradar'
            else:
                radartype = 'radar'

            # Type of boundary
            if parms_dict['imagetype'] == 'radius':
                for key in ('minlat', 'minlon', 'maxlat', 'maxlon', 'imagetype',):
                    del parms_dict[key]

            elif parms_dict['imagetype'] == 'boundingbox':
                for key in ('centerlat', 'centerlon', 'radius', 'imagetype',):
                    del parms_dict[key]

            else:
                for key in ('minlat', 'minlon', 'maxlat', 'maxlon', 'imagetype', 'centerlat', 'centerlon', 'radius',):
                    location = u"q/{0}".format(dev.pluginProps['location'])
                    name = ''
                    del parms_dict[key]

            # If Mercator is 0, del the key
            if not parms_dict['reproj.automerc']:
                del parms_dict['reproj.automerc']

            for k, v in parms_dict.iteritems():

                # Convert boolean props to 0/1 for URL encode.
                if str(v) == 'False':
                    v = 0

                elif str(v) == 'True':
                    v = 1

                # Create string of parms for URL encode.
                if len(parms) < 1:
                    parms += "{0}={1}".format(k, v)

                else:
                    parms += "&{0}={1}".format(k, v)

            source = 'http://api.wunderground.com/api/{0}/{1}/{2}{3}{4}?{5}'.format(self.pluginPrefs['apiKey'], radartype, location, name, '.gif', parms)
            if debug_level >= 3:
                self.debugLog(u"URL: {0}".format(source))
            destination = "/Library/Application Support/Perceptive Automation/Indigo {0}/IndigoWebServer/images/controls/static/{1}.gif".format(indigo.server.version.split('.')[0],
                                                                                                                                                dev.pluginProps['imagename'])
            try:
                r = requests.get(source, stream=True, timeout=10)
                self.debugLog(u"Image request status code: {0}".format(r.status_code))

                if r.status_code == 200:
                    with open(destination, 'wb') as img:

                        for chunk in r.iter_content(1024):
                            img.write(chunk)

                    if debug_level >= 2:
                        self.debugLog(u"Radar image source: {0}".format(source))
                        self.debugLog(u"Satellite image downloaded successfully.")

                    dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
                    dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")

                else:
                    self.errorLog(u"Error downloading image file: {0}".format(r.status_code))
                    raise NameError

            # If requests doesn't work for some reason, revert to urllib.
            except NameError:
                r = urllib.urlretrieve(source, destination)
                self.debugLog(u"Image request status code: {0}".format(r.getcode()))

            # Since this uses the API, go increment the call counter.
            self.callCount()

        except Exception as error:
            self.errorLog(u"Error downloading satellite image. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u"No comm")

    def getWeatherData(self, dev):
        """ Grab the JSON for the device. A separate call must be made for each
        weather device because the data are location specific. """

        debug_level = self.pluginPrefs['showDebugLevel']

        if debug_level >= 3:
            self.debugLog(u"getWeatherData() method called.")

        if dev.model not in ['Satellite Image Downloader', 'WUnderground Satellite Image Downloader']:
            try:

                config_menu_units = dev.pluginProps.get('configMenuUnits', '')

                try:
                    location = dev.pluginProps['location']

                except Exception as error:
                    self.debugLog(u"Exception retrieving location from device. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                    indigo.server.log(u"Missing location information for device: {0}. Attempting to automatically determine location using your IP address.".format(dev.name),
                                      type="WUnderground Info", isError=False)
                    location = "autoip"

                if location in self.masterWeatherDict.keys():
                    # We already have the data, so no need to get it again.
                    self.debugLog(u"  Location already in master weather dictionary.")

                else:
                    # We don't have this location's data yet. Go and get the data and add it to the masterWeatherDict.
                    #
                    # 03/30/15, modified by raneil. Improves the odds of dodging the "invalid literal for int() with base 16: ''")
                    # [http://stackoverflow.com/questions/10158701/how-to-capture-output-of-curl-from-python-script]
                    # switches to yesterday api instead of history_DATE api.
                    #url = (u"http://api.wunderground.com/api/{0}/geolookup/alerts_v11/almanac_v11/astronomy_v11/conditions_v11/forecast_v11/forecast10day_v11/hourly_v11/lang:{1}/"
                    #       u"yesterday_v11/tide_v11/q/{2}.json?apiref=97986dc4c4b7e764".format(self.pluginPrefs['apiKey'], self.pluginPrefs['language'], location))
                    #
                    # 06/19/2020: Modified by Leon Shaner.  Old WU API is completely defunct.  Switching to new WU API

                    if config_menu_units == "S": # URL modifier for "Standard" Units
                        wu_units='e'
                    else: # URL modifier for all else (metric
                        wu_units='m'

                    url = (u"https://api.weather.com/v2/pws/observations/current?stationId={0}&format=json&units={1}&apiKey={2}".format(location, wu_units, self.pluginPrefs['apiKey']))

                    # Debug output can contain sensitive data.
                    if debug_level >= 3:
                        self.debugLog(u"  URL prepared for API call: {0}".format(url))
                    else:
                        self.debugLog(u"Weather Underground URL suppressed. Set debug level to [High] to write it to the log.")
                    self.debugLog(u"Getting weather data for location: {0}".format(location))

                    # Start download timer.
                    get_data_time = dt.datetime.now()

                    # If requests doesn't work for some reason, try urllib2 instead.
                    try:
                        f = requests.get(url, timeout=10)
                        simplejson_string = f.text  # We convert the file to a json object below, so we don't use requests' built-in decoder.

                    except NameError:
                        try:
                            # Connect to Weather Underground and retrieve data.
                            socket.setdefaulttimeout(30)
                            f = urllib2.urlopen(url)
                            simplejson_string = f.read()

                        # ==============================================================
                        # Communication error handling:
                        # ==============================================================
                        except urllib2.HTTPError as error:
                            self.debugLog(u"Unable to reach Weather Underground - HTTPError (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno,
                                                                                                                                                        error))
                            for dev in indigo.devices.itervalues("self"):
                                dev.updateStateOnServer("onOffState", value=False, uiValue=u" ")
                            return

                        except urllib2.URLError as error:
                            self.debugLog(u"Unable to reach Weather Underground. - URLError (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno,
                                                                                                                                                        error))
                            for dev in indigo.devices.itervalues("self"):
                                dev.updateStateOnServer("onOffState", value=False, uiValue=u" ")
                            return

                        except Exception as error:
                            self.debugLog(u"Unable to reach Weather Underground. - Exception (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno,
                                                                                                                                                         error))
                            for dev in indigo.devices.itervalues("self"):
                                dev.updateStateOnServer("onOffState", value=False, uiValue=u" ")
                            return

                    # Report results of download timer.
                    data_cycle_time = (dt.datetime.now() - get_data_time)
                    data_cycle_time = (dt.datetime.min + data_cycle_time).time()

                    if debug_level >= 1 and simplejson_string != "":
                        self.debugLog(u"[{0} download: {1} seconds]".format(dev.name, data_cycle_time.strftime('%S.%f')))

                    # Load the JSON data from the file.
                    try:
                        parsed_simplejson = simplejson.loads(simplejson_string, encoding="utf-8")
                    except Exception as error:
                        self.debugLog(u"Unable to decode data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
                        parsed_simplejson = {}

                    # Add location JSON to master weather dictionary.
                    self.debugLog(u"Adding weather data for {0} to Master Weather Dictionary.".format(location))
                    self.masterWeatherDict[location] = parsed_simplejson

                    # Go increment (or reset) the call counter.
                    self.callCount()

            except Exception as error:
                self.debugLog(u"Unable to reach Weather Underground. Error: (Line {0}  {1}) Sleeping until next scheduled poll.".format(sys.exc_traceback.tb_lineno, error))

                # Unable to fetch the JSON. Mark all devices as 'false'.
                for dev in indigo.devices.itervalues("self"):
                    if dev.enabled:
                        dev.updateStateOnServer('onOffState', value=False, uiValue=u"No comm")

                self.wuOnline = False

        # We could have come here from several different places. Return to whence we came to further process the weather data.
        self.wuOnline = True
        return self.masterWeatherDict

    def itemListTemperatureFormat(self, val):
        """ Adjusts the decimal precision of the temperature value for the
        Indigo Item List. Note: this method needs to return a string rather
        than a Unicode string (for now.) """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"itemListTemperatureFormat(self, val={0})".format(val))

        try:
            if self.pluginPrefs.get('itemListTempDecimal', 0) == 0:
                val = float(val)
                return u"{0:0.0f}".format(val)
            else:
                return u"{0}".format(val)

        except ValueError:
            return u"{0}".format(val)

    def listOfDevices(self, typeId, valuesDict, targetId, devId):
        """ listOfDevices returns a list of plugin devices. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"listOfDevices method() called.")
            self.debugLog(u"typeID: {0}".format(typeId))
            self.debugLog(u"targetId: {0}".format(targetId))
            self.debugLog(u"devId: {0}".format(devId))
            self.debugLog(u"============ valuesDict ============\n")

            for key, value in valuesDict.iteritems():
                self.debugLog(u"{0}: {1}".format(key, value))

        return [(dev.id, dev.name) for dev in indigo.devices.itervalues(filter='self')]

    def nestedLookup(self, obj, keys, default=u"Not available"):
        """The nestedLookup() method is used to extract the relevant data from
        the Weather Underground JSON return. The JSON is known to sometimes be
        inconsistent in the form of sometimes missing keys. This method allows
        for a default value to be used in instances where a key is missing. The
        method call can rely on the default return, or send an optional
        'default=some_value' parameter.

        Credit: Jared Goguen at StackOverflow for initial implementation."""

        current = obj

        for key in keys:
            current = current if isinstance(current, list) else [current]

            try:
                current = next(sub[key] for sub in current if key in sub)

            except StopIteration:
                return default

        return current


    def parseWeatherData(self, dev):
        """ The parseWeatherData() method takes weather data and parses it to
        Weather Device states. """

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"parseWeatherData(self, dev) method called.")

        # Reload the date and time preferences in case they've changed.
        self.date_format = self.Formatter.dateFormat()
        self.time_format = self.Formatter.timeFormat()

        try:

            config_itemlist_ui_units = dev.pluginProps.get('itemListUiUnits', '')
            config_menu_units        = dev.pluginProps.get('configMenuUnits', '')
            config_distance_units    = dev.pluginProps.get('distanceUnits', '')
            location                 = dev.pluginProps['location']
            pressure_units           = dev.pluginProps.get('pressureUnits', '')

            weather_data = self.masterWeatherDict[location]

            # 06/19/2020:  Updated by Leon Shaner for new WU API
            #current_observation_epoch = self.nestedLookup(weather_data['observations'][0], keys=('epoch'))
            current_observation_epoch = weather_data['observations'][0]['epoch']

            #current_observation_time  = self.nestedLookup(weather_data['observations'][0], keys=('obsTimeLocal'))
            current_observation_time  = weather_data['observations'][0]['obsTimeLocal']

            # WU response now includes only a single unit, but with same property names
            # Therefore requires different handling, based on unit requested

            # Same for all units
            uv_index                  = weather_data['observations'][0]['uv']
            station_id                = weather_data['observations'][0]['stationID']
            relative_humidity         = weather_data['observations'][0]['humidity']
            wind_degrees              = weather_data['observations'][0]['winddir']

            # "Standard" units
            if config_menu_units == 'S':
                current_temp_f            = self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'temp',))
                dew_point_f               = int(self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'dewpt',)))
                heat_index_f              = self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'heatIndex',))
                precip_rate_in            = self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'precipRate',))
                precip_today_in           = self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'precipTotal',))
                pressure_in               = self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'pressure',))
                wind_chill_f              = self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'windChill',))
                wind_gust_mph             = self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'windGust',))
                wind_speed_mph            = self.nestedLookup(weather_data['observations'][0], keys=('imperial', 'windSpeed',))
                temp_f, temp_f_ui = self.fixCorruptedData(state_name=u'temp_f', val=current_temp_f)
                temp_f_ui = self.uiFormatTemperature(dev=dev, state_name=u"tempF (S)", val=temp_f_ui)
                dev.updateStateOnServer('temp', value=temp_f, uiValue=temp_f_ui)
                icon_value = u"{0}".format(str(round(temp_f, 0)).replace('.', ''))
                dev.updateStateOnServer('tempIcon', value=icon_value)

                # Displays F temperature in the Indigo Item List display
                display_value = u"{0} \N{DEGREE SIGN}F".format(self.itemListTemperatureFormat(val=temp_f))

                # Dew Point (integer: -20 -- units: Fahrenheit)
                dewpoint, dewpoint_ui = self.fixCorruptedData(state_name=u"dewpointF (S)", val=dew_point_f)
                dewpoint_ui = self.uiFormatTemperature(dev=dev, state_name=u"dewpointF (S)", val=dewpoint_ui)
                dev.updateStateOnServer('dewpoint', value=dewpoint, uiValue=dewpoint_ui)
                # Displays F temperature in the Indigo Item List display
                display_value = u"{0} \N{DEGREE SIGN}F".format(self.itemListTemperatureFormat(val=dew_point_f))

                # Heat Index (string: "20", "NA" -- units: Fahrenheit)
                heat_index, heat_index_ui = self.fixCorruptedData(state_name=u"heatIndexF (S)", val=heat_index_f)
                heat_index_ui = self.uiFormatTemperature(dev=dev, state_name=u"heatIndexF (S)", val=heat_index_ui)
                dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=heat_index_ui)
                display_value = u"{0} \N{DEGREE SIGN}F".format(self.itemListTemperatureFormat(val=heat_index_f))

                # Wind Chill (string: "17" -- units: Fahrenheit)
                windchill, windchill_ui = self.fixCorruptedData(state_name=u"windChillF (S)", val=wind_chill_f)
                windchill_ui = self.uiFormatTemperature(dev=dev, state_name=u"windChillF (S)", val=windchill_ui)
                dev.updateStateOnServer('windchill', value=windchill, uiValue=windchill_ui)
                display_value = u"{0} \N{DEGREE SIGN}F".format(self.itemListTemperatureFormat(val=wind_chill_f))

                # Barometric Pressure (string: "30.25" -- units: inches of mercury)
                pressure, pressure_ui = self.fixCorruptedData(state_name=u"pressure (S)", val=pressure_in)
                dev.updateStateOnServer('pressure', value=pressure, uiValue=u"{0}{1}".format(pressure_ui, pressure_units))
                dev.updateStateOnServer('pressureIcon', value=pressure_ui.replace('.', ''))

                # Precipitation Today (string: "0", "0.5" -- units: inches)
                precip_today, precip_today_ui = self.fixCorruptedData(state_name=u"precipToday (I)", val=precip_today_in)
                precip_today_ui = self.uiFormatRain(dev=dev, state_name=u"precipToday (I)", val=precip_today_ui)
                dev.updateStateOnServer('precip_today', value=precip_today, uiValue=precip_today_ui)

                # Wind Gust (string: "19.3" -- units: kph)
                wind_gust_mph, wind_gust_mph_ui = self.fixCorruptedData(state_name=u"windGust (MPH)", val=wind_gust_mph)
                wind_speed_mph, wind_speed_mph_ui = self.fixCorruptedData(state_name=u"windGust (MPH)", val=wind_speed_mph)
                dev.updateStateOnServer('windGust', value=wind_gust_mph, uiValue=self.uiFormatWind(dev=dev, state_name=u"windGust", val=wind_gust_mph_ui))
                dev.updateStateOnServer('windSpeed', value=wind_speed_mph, uiValue=self.uiFormatWind(dev=dev, state_name=u"windSpeed", val=wind_speed_mph_ui))
                dev.updateStateOnServer('windGustIcon', value=unicode(round(wind_gust_mph, 1)).replace('.', ''))
                dev.updateStateOnServer('windSpeedIcon', value=unicode(round(wind_speed_mph, 1)).replace('.', ''))
                dev.updateStateOnServer('windString', value=u"From {0} degrees at {1} MPH Gusting to {2} MPH".format(wind_degrees, wind_speed_mph, wind_gust_mph))
                dev.updateStateOnServer('windShortString', value=u"{0} degrees at {1}".format(wind_degrees, wind_speed_mph))
                dev.updateStateOnServer('windStringMetric', value=u" ")

            else:
                current_temp_c            = self.nestedLookup(weather_data['observations'][0], keys=('metric', 'temp',))
                dew_point_c               = int(self.nestedLookup(weather_data['observations'][0], keys=('metric', 'dewpt',)))
                heat_index_c              = self.nestedLookup(weather_data['observations'][0], keys=('metric', 'heatIndex',))
                precip_rate_m             = self.nestedLookup(weather_data['observations'][0], keys=('metric', 'precipRate',))
                precip_today_m            = self.nestedLookup(weather_data['observations'][0], keys=('metric', 'precipTotal',))
                pressure_mb               = self.nestedLookup(weather_data['observations'][0], keys=('metric', 'pressure',))
                wind_chill_c              = self.nestedLookup(weather_data['observations'][0], keys=('metric', 'windChill',))
                wind_gust_kph             = self.nestedLookup(weather_data['observations'][0], keys=('metric', 'windGust',))
                wind_speed_kph            = self.nestedLookup(weather_data['observations'][0], keys=('metric', 'windSpeed',))
                temp_c, temp_c_ui = self.fixCorruptedData(state_name=u'temp_c', val=current_temp_c)
                temp_c_ui = self.uiFormatTemperature(dev=dev, state_name=u"tempC (M, MS, I)", val=temp_c_ui)
                dev.updateStateOnServer('temp', value=temp_c, uiValue=temp_c_ui)
                icon_value = u"{0}".format(str(round(temp_c, 0)).replace('.', ''))
                dev.updateStateOnServer('tempIcon', value=icon_value)

                # Displays C temperature in the Indigo Item List display
                display_value = u"{0} \N{DEGREE SIGN}C".format(self.itemListTemperatureFormat(val=temp_c))

                # Dew Point (integer: -20 -- units: Centigrade)
                dewpoint, dewpoint_ui = self.fixCorruptedData(state_name=u"dewpointC (M, MS)", val=dew_point_c)
                dewpoint_ui = self.uiFormatTemperature(dev=dev, state_name=u"dewpointC (M, MS)", val=dewpoint_ui)
                dev.updateStateOnServer('dewpoint', value=dewpoint, uiValue=dewpoint_ui)
                display_value = u"{0} \N{DEGREE SIGN}C".format(self.itemListTemperatureFormat(val=dew_point_c))

                # Heat Index (string: "20", "NA" -- units: Centigrade)
                heat_index, heat_index_ui = self.fixCorruptedData(state_name=u"heatIndexC (M, MS)", val=heat_index_c)
                heat_index_ui = self.uiFormatTemperature(dev=dev, state_name=u"heatIndexC (M, MS)", val=heat_index_ui)
                dev.updateStateOnServer('heatIndex', value=heat_index, uiValue=heat_index_ui)
                display_value = u"{0} \N{DEGREE SIGN}C".format(self.itemListTemperatureFormat(val=heat_index_c))

                # Wind Chill (string: "17" -- units: Centigrade)
                windchill, windchill_ui = self.fixCorruptedData(state_name=u"windChillC (M, MS)", val=wind_chill_c)
                windchill_ui = self.uiFormatTemperature(dev=dev, state_name=u"windChillC (M, MS)", val=windchill_ui)
                dev.updateStateOnServer('windchill', value=windchill, uiValue=windchill_ui)
                display_value = u"{0} \N{DEGREE SIGN}C".format(self.itemListTemperatureFormat(val=wind_chill_c))

                # Precipitation Today (string: "0", "2" -- units: mm)
                precip_today, precip_today_ui = self.fixCorruptedData(state_name=u"precipMM (M, MS)", val=precip_today_m)
                precip_today_ui = self.uiFormatRain(dev=dev, state_name=u"precipToday (M, MS)", val=precip_today_ui)
                dev.updateStateOnServer('precip_today', value=precip_today, uiValue=precip_today_ui)

                # Wind Speed
                # Wind Gust (string: "19.3" -- units: kph)
                wind_gust_kph, wind_gust_kph_ui = self.fixCorruptedData(state_name=u"windGust (KPH)", val=wind_gust_kph)
                wind_gust_mps, wind_gust_mps_ui = self.fixCorruptedData(state_name=u"windGust (MPS)", val=int(wind_gust_kph * 0.277778))
                wind_speed_kph, wind_speed_kph_ui = self.fixCorruptedData(state_name=u"windGust (KPH)", val=wind_speed_kph)
                dev.updateStateOnServer('windGust', value=wind_gust_kph, uiValue=self.uiFormatWind(dev=dev, state_name=u"windGust", val=wind_gust_kph_ui))
                dev.updateStateOnServer('windSpeed', value=wind_speed_kph, uiValue=self.uiFormatWind(dev=dev, state_name=u"windSpeed", val=wind_speed_kph_ui))
                dev.updateStateOnServer('windGustIcon', value=unicode(round(wind_gust_kph, 1)).replace('.', ''))
                dev.updateStateOnServer('windSpeedIcon', value=unicode(round(wind_speed_kph, 1)).replace('.', ''))
                dev.updateStateOnServer('windString', value=u"From {0} degrees at {1} KPH Gusting to {2} KPH".format(wind_degrees, wind_speed_kph, wind_gust_kph))
                dev.updateStateOnServer('windShortString', value=u"{0} degress at {1}".format(wind_degrees, wind_speed_kph))
                dev.updateStateOnServer('windStringMetric', value=u"From the {0} at {1} KPH Gusting to {2} KPH".format(wind_degrees, wind_speed_kph, wind_gust_kph))


########### Continuing with properties that apply to all unit specifications

            dev.updateStateOnServer('onOffState', value=True, uiValue=display_value)
            dev.updateStateOnServer('stationID', value=station_id, uiValue=station_id)

            # Current Observation Time (string: "Last Updated on MONTH DD, HH:MM AM/PM TZ")
            dev.updateStateOnServer('currentObservation', value=current_observation_time, uiValue=current_observation_time)

            # Current Observation Time 24 Hour (string)
            current_observation_24hr = time.strftime("{0} {1}".format(self.date_format, self.time_format), time.localtime(float(current_observation_epoch)))
            dev.updateStateOnServer('currentObservation24hr', value=current_observation_24hr)

#            # Current Observation Time Epoch (string)
#            dev.updateStateOnServer('currentObservationEpoch', value=current_observation_epoch, uiValue=current_observation_epoch)

#            #Solar Radiation (string: "0" or greater. Not always provided as a value that can float (sometimes = ""). Some sites don't report it.)
#            s_rad, s_rad_ui = self.fixCorruptedData(state_name=u"Solar Radiation", val=solar_radiation)
#            dev.updateStateOnServer('solarradiation', value=s_rad, uiValue=s_rad_ui)


#            # Wind direction (integer: 0 - 359 -- units: degrees)
#            wind_degrees, wind_degrees_ui = self.fixCorruptedData(state_name=u"windDegrees", val=wind_degrees)
#            dev.updateStateOnServer('windDegrees', value=int(wind_degrees), uiValue=str(int(wind_degrees)))

            # Relative Humidity (string: "80%")
            relative_humidity, relative_humidity_ui = self.fixCorruptedData(state_name=u"relativeHumidity", val=relative_humidity)
            relative_humidity_ui = self.uiFormatPercentage(dev=dev, state_name=u"relativeHumidity", val=relative_humidity_ui)
            dev.updateStateOnServer('relativeHumidity', value=relative_humidity, uiValue=relative_humidity_ui)

            new_props = dev.pluginProps
            new_props['address'] = station_id
            dev.replacePluginPropsOnServer(new_props)

            dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensorOn)

        except IndexError:
            self.errorLog(u"Note: List index out of range. This is likely normal.")

        except Exception as error:
            self.errorLog(u"Problem parsing weather device data. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            dev.updateStateOnServer('onOffState', value=False, uiValue=u" ")
            dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOff)

    def refreshWeatherData(self):
        """ This method refreshes weather data for all devices based on a
        WUnderground general cycle, Action Item or Plugin Menu call. """

        api_key = self.pluginPrefs['apiKey']
        daily_call_limit_reached = self.pluginPrefs.get('dailyCallLimitReached', False)
        sleep_time = self.pluginPrefs.get('downloadInterval', 15)
        self.wuOnline = True

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"refreshWeatherData() method called.")

        # Check to see if the daily call limit has been reached.
        try:

            if daily_call_limit_reached:
                self.callDay()

            elif not daily_call_limit_reached:
                self.callDay()

                self.masterWeatherDict = {}

                for dev in indigo.devices.itervalues("self"):

                    if not self.wuOnline:
                        break

                    if not dev:
                        # There are no WUnderground devices, so go to sleep.
                        indigo.server.log(u"There aren't any devices to poll yet. Sleeping.", type="WUnderground Status")
                        self.sleep(sleep_time)

                    elif not dev.configured:
                        # A device has been created, but hasn't been fully configured yet.
                        indigo.server.log(u"A device has been created, but is not fully configured. Sleeping for a minute while you finish.", type="WUnderground Status")
                        self.sleep(60)

                    if api_key in ["", "API Key"]:
                        self.errorLog(u"The plugin requires an API Key. See help for details.")
                        dev.updateStateOnServer('onOffState', value=False, uiValue=u"{0}".format("No key."))
                        self.sleep(sleep_time)

                    elif not dev.enabled:
                        self.debugLog(u"{0}: device communication is disabled. Skipping.".format(dev.name))
                        dev.updateStateOnServer('onOffState', value=False, uiValue=u"{0}".format("Disabled"))

                    elif dev.enabled:
                        self.debugLog(u"Parse weather data for device: {0}".format(dev.name))
                        # Get weather data from Weather Underground
                        dev.updateStateOnServer('onOffState', value=True, uiValue=u" ")

                        if dev.model not in ['Satellite Image Downloader', 'WUnderground Radar', 'WUnderground Satellite Image Downloader']:

                            location = dev.pluginProps['location']

                            self.getWeatherData(dev)
                            if self.pluginPrefs['showDebugLevel'] >= 3:
                                self.dumpTheJSON()
                                self.dumpTheDict()

                            # If we've successfully downloaded data from Weather Underground, let's unpack it and assign it to the relevant device.
                            try:
                                # If a site location query returns a site unknown (in other words 'querynotfound' result, notify the user).
                                response = self.masterWeatherDict[location]
                                if response == '{}':
                                    self.errorLog(u"Location query for {0} not found. Please ensure that device location follows examples precisely.".format(dev.name))
                                    dev.updateStateOnServer('onOffState', value=False, uiValue=u"Bad Loc")

                            except (KeyError, Exception) as error:
                                # Weather device types. There are multiples of these because the names of the device models evolved over time.
                                # If the error key is not present, that's good. Continue.
                                error = u"{0}".format(error)
                                if error == "'error'":
                                    pass
                                else:
                                    self.debugLog(u"Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

                            # Compare last data epoch to the one we just downloaded. Proceed if the data are newer.
                            # Note: WUnderground have been known to send data that are 5-6 months old. This flag helps ensure that known data are retained if the new data is not
                            # actually newer that what we already have.

                            try:
                                # New devices may not have an epoch value yet.
                                device_epoch = dev.states['currentObservationEpoch']
                                try:
                                    device_epoch = int(device_epoch)
                                except ValueError:
                                    device_epoch = 0

                                # If we don't know the age of the data, we don't update.
                                try:
                                    weather_data_epoch = int(self.masterWeatherDict[location]['observations'][0]['epoch'])
                                except ValueError:
                                    weather_data_epoch = 0

                                if self.pluginPrefs['showDebugLevel'] >= 2:
                                    self.debugLog(u"Info: weather_data_epoch={0}\n".format(weather_data_epoch))

                                good_time = device_epoch <= weather_data_epoch
                                if not good_time:
                                    indigo.server.log(u"Latest data are older than data we already have. Skipping {0} update.".format(dev.name), type="WUnderground Status")
                            except KeyError:
                                indigo.server.log(u"{0} cannot determine age of data. Skipping until next scheduled poll.".format(dev.name), type="WUnderground Status")
                                good_time = False

                            # If the weather dict is not empty, the data are newer than the data we already have, an
                            # the user doesn't want to ignore estimated weather conditions, let's update the devices.
                            if self.masterWeatherDict != {} and good_time:

                                # Weather devices.
                                if dev.model in ['WUnderground Device', 'WUnderground Weather', 'WUnderground Weather Device', 'Weather Underground', 'Weather']:
                                    self.parseWeatherData(dev)
                                    dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensorOn)

                        # Image Downloader devices.
                        elif dev.model in ['Satellite Image Downloader', 'WUnderground Satellite Image Downloader']:
                            self.getSatelliteImage(dev)

                        # WUnderground Radar devices.
                        elif dev.model in ['WUnderground Radar']:
                            self.getWUradar(dev)

            self.debugLog(u"Locations Polled: {0}{1}Weather Underground cycle complete.".format(self.masterWeatherDict.keys(), pad_log))

        except Exception as error:
            self.errorLog(u"Problem parsing Weather data. Dev: {0} (Line: {1} Error: {2})".format(dev.name, sys.exc_traceback.tb_lineno, error))

    def runConcurrentThread(self):
        """ Main plugin thread. """

        self.debugLog(u"runConcurrentThread initiated.")

        download_interval = int(self.pluginPrefs.get('downloadInterval', 15))

        if self.pluginPrefs['showDebugLevel'] >= 2:
            self.debugLog(u"Sleeping for 5 seconds to give the host process a chance to catch up (if it needs to.)")
        self.sleep(5)

        try:
            while True:
                start_time = dt.datetime.now()

                self.refreshWeatherData()
                self.triggerFireOfflineDevice()

                # Report results of download timer.
                plugin_cycle_time = (dt.datetime.now() - start_time)
                plugin_cycle_time = (dt.datetime.min + plugin_cycle_time).time()

                self.debugLog(u"[Plugin execution time: {0} seconds]".format(plugin_cycle_time.strftime('%S.%f')))
                self.sleep(download_interval)

        except self.StopThread as error:
            self.debugLog(u"StopThread: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))
            self.debugLog(u"Stopping WUnderground Plugin thread.")

    def shutdown(self):
        """ Plugin shutdown routines. """

        self.debugLog(u"Plugin shutdown() method called.")

    def startup(self):
        """ Plugin startup routines. """

        self.debugLog(u"Plugin startup called.")

    def triggerFireOfflineDevice(self):
        """ The triggerFireOfflineDevice method will examine the time of the
        last weather location update and, if the update exceeds the time delta
        specified in a WUnderground Plugin Weather Location Offline trigger,
        the trigger will be fired. The plugin examines the value of the
        latest "currentObservationEpoch" and *not* the Indigo Last Update
        value.

        An additional event that will cause a trigger to be fired is if the
        weather location temperature is less than -55 (Weather Underground
        will often set a value to a variation of -99 (-55 C) to indicate that
        a data value is invalid.

        Note that the trigger will only fire during routine weather update
        cycles and will not be triggered when a data refresh is called from
        the Indigo Plugins menu."""

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"triggerFireOfflineDevice method() called.")

        try:
            for dev in indigo.devices.itervalues(filter='self'):
                if str(dev.id) in self.masterTriggerDict.keys():

                    if dev.enabled:

                        trigger_id = self.masterTriggerDict[str(dev.id)][1]  # Indigo trigger ID

                        if indigo.triggers[trigger_id].enabled:

                            if indigo.triggers[trigger_id].pluginTypeId == 'weatherSiteOffline':

                                offline_delta = dt.timedelta(minutes=int(self.masterTriggerDict[str(dev.id)][0]))

                                # Convert currentObservationEpoch to a localized datetime object
                                current_observation = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(dev.states['currentObservationEpoch'])))
                                current_observation = dt.datetime.strptime(current_observation, '%Y-%m-%d %H:%M:%S')

                                # Time elapsed since last observation
                                diff = indigo.server.getTime() - current_observation

                                # If the observation is older than offline_delta
                                if diff >= offline_delta:
                                    indigo.server.log(u"{0} location appears to be offline for {1:}".format(dev.name, diff), type="WUnderground Status")
                                    indigo.trigger.execute(trigger_id)

                                # If the temperature observation is lower than -55 C
                                elif dev.states['temp'] <= -55.0:
                                    indigo.server.log(u"{0} location appears to be offline (reported temperature).".format(dev.name), type="WUnderground Status")
                                    indigo.trigger.execute(trigger_id)

                            if indigo.triggers[trigger_id].pluginTypeId == 'weatherAlert':

                                # If at least one severe weather alert exists for the location
                                if dev.states['alertStatus'] == 'true':
                                    indigo.server.log(u"{0} location has at least one severe weather alert.".format(dev.name), type="WUnderground Info")
                                    indigo.trigger.execute(trigger_id)

        except KeyError:
            pass

    def triggerStartProcessing(self, trigger):
        """ triggerStartProcessing is called when the plugin is started. The
        method builds a global dict: {dev.id: (delay, trigger.id) """

        dev_id = str(trigger.pluginProps['listOfDevices'])

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"triggerStartProcessing method() called.")

        try:
            self.masterTriggerDict[dev_id] = (trigger.pluginProps['offlineTimer'], trigger.id)

        except KeyError:
            self.masterTriggerDict[dev_id] = (u'0', trigger.id)

    def triggerStopProcessing(self, trigger):
        """"""

        if self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"triggerStopProcessing method() called.")
            self.debugLog(u"trigger: {0}".format(trigger))

    def uiFormatPercentage(self, dev, state_name, val):
        """ Adjusts the decimal precision of percentage values for display in
        control pages, etc. """

        humidity_decimal = int(self.pluginPrefs.get('uiHumidityDecimal', 1))
        percentage_units = dev.pluginProps.get('percentageUnits', '')

        try:
            return u"{0:0.{1}f}{2}".format(float(val), int(humidity_decimal), percentage_units)

        except ValueError as error:
            self.debugLog(u"Error formatting uiPercentage: {0}".format(error))
            return u"{0}{1}".format(val, percentage_units)

    def uiFormatRain(self, dev, state_name, val):
        """ Adjusts the decimal precision of rain values for display in control
        pages, etc. """

        try:
            rain_units = dev.pluginProps.get('rainUnits', '')
        except KeyError:
            rain_units = dev.pluginProps.get('rainAmountUnits', '')

        if val in ["NA", "N/A", "--", ""]:
            return val

        try:
            return u"{0}{1}".format(val, rain_units)

        except ValueError as error:
            self.debugLog(u"Error formatting uiRain: {0}".format(error))
            return u"{0}".format(val)

    def uiFormatSnow(self, dev, state_name, val):
        """ Adjusts the decimal precision of snow values for display in control
        pages, etc. """

        if val in ["NA", "N/A", "--", ""]:
            return val

        try:
            return u"{0}{1}".format(val, dev.pluginProps.get('snowAmountUnits', ''))

        except ValueError as error:
            self.debugLog(u"Error formatting uiSnow: {0}".format(error))
            return u"{0}".format(val)

    def uiFormatTemperature(self, dev, state_name, val):
        """ Adjusts the decimal precision of certain temperature values and
        appends the desired units string for display in control pages, etc. """

        temp_decimal = int(self.pluginPrefs.get('uiTempDecimal', 1))
        temperature_units = dev.pluginProps.get('temperatureUnits', '')

        try:
            return u"{0:0.{1}f}{2}".format(float(val), int(temp_decimal), temperature_units)

        except ValueError as error:
            self.debugLog(u"Can not format uiTemperature. This is likely normal.".format(error))
            return u"--"

    def uiFormatWind(self, dev, state_name, val):
        """ Adjusts the decimal precision of certain wind values for display
        in control pages, etc. """

        wind_decimal = self.pluginPrefs.get('uiWindDecimal', 1)
        wind_units   = dev.pluginProps.get('windUnits', '')

        try:
            return u"{0:0.{1}f}{2}".format(float(val), int(wind_decimal), wind_units)

        except ValueError as error:
            self.debugLog(u"Error formatting uiTemperature: {0}".format(error))
            return u"{0}".format(val)

    def validateDeviceConfigUi(self, valuesDict, typeID, devId):
        """ Validate select device config menu settings. """

        self.debugLog(u"validateDeviceConfigUi() method called.")

        error_msg_dict = indigo.Dict()

        try:

            # WUnderground Radar Devices
            if typeID == 'wundergroundRadar':

                if valuesDict['imagename'] == "" or valuesDict['imagename'].isspace():
                    error_msg_dict['imagename'] = u"You must enter a valid image name."
                    error_msg_dict['showAlertText'] = u"Image Name Error.\n\nYou must enter a valid image name."
                    return False, valuesDict, error_msg_dict

                try:
                    height = int(valuesDict['height'])
                    width = int(valuesDict['width'])
                except ValueError:
                    error_msg_dict['showAlertText'] = u"Image Size Error.\n\nImage size values must be real numbers greater than zero."
                    return False, valuesDict, error_msg_dict

                if not height >= 100:
                    error_msg_dict['height'] = u"The image height must be at least 100 pixels."
                    error_msg_dict['showAlertText'] = u"Height Error.\n\nThe image height must be at least 100 pixels."
                    return False, valuesDict, error_msg_dict

                if not width >= 100:
                    error_msg_dict['width'] = u"The image width must be at least 100 pixels."
                    error_msg_dict['showAlertText'] = u"Width Error.\n\nThe image width must be at least 100 pixels."
                    return False, valuesDict, error_msg_dict

                if not height == width:
                    error_msg_dict['height'] = u"Image height and width must be the same."
                    error_msg_dict['width'] = u"Image height and width must be the same."
                    error_msg_dict['showAlertText'] = u"Size Error.\n\nFor now, the plugin only supports square radar images. Image height and width must be the same."
                    return False, valuesDict, error_msg_dict

                try:
                    num = int(valuesDict['num'])
                except ValueError:
                    error_msg_dict['num'] = u"The number of frames must be between 1 - 15."
                    error_msg_dict['showAlertText'] = u"Frames Error.\n\nThe number of frames must be between 1 - 15."
                    return False, valuesDict, error_msg_dict

                if not 0 < num <= 15:
                    error_msg_dict['num'] = u"The number of frames must be between 1 - 15."
                    error_msg_dict['showAlertText'] = u"Frames Error.\n\nThe number of frames must be between 1 - 15."
                    return False, valuesDict, error_msg_dict

                try:
                    timelabelx = int(valuesDict['timelabelx'])
                    timelabely = int(valuesDict['timelabely'])
                except ValueError:
                    error_msg_dict['showAlertText'] = u"Time Stamp Label Error.\n\nThe time stamp location settings must be values greater than or equal to zero."
                    return False, valuesDict, error_msg_dict

                if not timelabelx >= 0:
                    error_msg_dict['timelabelx'] = u"The time stamp location setting must be a value greater than or equal to zero."
                    error_msg_dict['showAlertText'] = u"Time Stamp Label Error.\n\nThe time stamp location setting must be a value greater than or equal to zero."
                    return False, valuesDict, error_msg_dict

                if not timelabely >= 0:
                    error_msg_dict['timelabely'] = u"The time stamp location setting must be a value greater than or equal to zero."
                    error_msg_dict['showAlertText'] = u"Time Stamp Label Error.\n\nThe time stamp location setting must be a value greater than or equal to zero."
                    return False, valuesDict, error_msg_dict

                # Image Type: Bounding Box
                if valuesDict['imagetype'] == 'boundingbox':

                    try:
                        maxlat = float(valuesDict['maxlat'])
                        maxlon = float(valuesDict['maxlon'])
                        minlat = float(valuesDict['minlat'])
                        minlon = float(valuesDict['minlon'])
                    except ValueError:
                        error_msg_dict['showAlertText'] = u"Lat/Long Value Error.\n\nLatitude and Longitude values must be expressed as real numbers. Hover over each field to see " \
                                                          u"descriptions of allowable values."
                        return False, valuesDict, error_msg_dict

                    if not -90.0 <= minlat <= 90.0:
                        error_msg_dict['minlat'] = u"The Min Lat must be between -90.0 and 90.0."
                        error_msg_dict['showAlertText'] = u"Latitude Error.\n\nMin Lat must be between -90.0 and 90.0."
                        return False, valuesDict, error_msg_dict

                    if not -90.0 <= maxlat <= 90.0:
                        error_msg_dict['maxlat'] = u"The Max Lat must be between -90.0 and 90.0."
                        error_msg_dict['showAlertText'] = u"Latitude Error.\n\nMax Lat must be between -90.0 and 90.0."
                        return False, valuesDict, error_msg_dict

                    if not -180.0 <= minlon <= 180.0:
                        error_msg_dict['minlon'] = u"The Min Long must be between -180.0 and 180.0."
                        error_msg_dict['showAlertText'] = u"Longitude Error.\n\nMin Long must be between -180.0 and 180.0."
                        return False, valuesDict, error_msg_dict

                    if not -180.0 <= maxlon <= 180.0:
                        error_msg_dict['maxlon'] = u"The Max Long must be between -180.0 and 180.0."
                        error_msg_dict['showAlertText'] = u"Longitude Error.\n\nMax Long must be between -180.0 and 180.0."
                        return False, valuesDict, error_msg_dict

                    if abs(minlat) > abs(maxlat):
                        error_msg_dict['minlat'] = u"The Max Lat must be greater than the Min Lat."
                        error_msg_dict['maxlat'] = u"The Max Lat must be greater than the Min Lat."
                        error_msg_dict['showAlertText'] = u"Latitude Error.\n\nMax Lat must be greater than the Min Lat."
                        return False, valuesDict, error_msg_dict

                    if abs(minlon) > abs(maxlon):
                        error_msg_dict['minlon'] = u"The Max Long must be greater than the Min Long."
                        error_msg_dict['maxlon'] = u"The Max Long must be greater than the Min Long."
                        error_msg_dict['showAlertText'] = u"Longitude Error.\n\nMax Long must be greater than the Min Long."
                        return False, valuesDict, error_msg_dict

                elif valuesDict['imagetype'] == 'radius':
                    try:
                        centerlat = float(valuesDict['centerlat'])
                        centerlon = float(valuesDict['centerlon'])
                    except ValueError:
                        error_msg_dict['showAlertText'] = u"Lat/Long Value Error.\n\nLatitude and Longitude values must be expressed as real numbers. Hover over each field to see " \
                                                          u"descriptions of allowable values."
                        return False, valuesDict, error_msg_dict

                    try:
                        radius = float(valuesDict['radius'])
                    except ValueError:
                        error_msg_dict['showAlertText'] = u"Radius Value Error.\n\nThe radius value must be a real number greater than zero"
                        return False, valuesDict, error_msg_dict

                    if not -90.0 <= centerlat <= 90.0:
                        error_msg_dict['centerlat'] = u"Center Lat must be between -90.0 and 90.0."
                        error_msg_dict['showAlertText'] = u"Center Lat Error.\n\nCenter Lat must be between -90.0 and 90.0."
                        return False, valuesDict, error_msg_dict

                    if not -180.0 <= centerlon <= 180.0:
                        error_msg_dict['centerlon'] = u"Center Long must be between -180.0 and 180.0."
                        error_msg_dict['showAlertText'] = u"Center Long Error.\n\nCenter Long must be between -180.0 and 180.0."
                        return False, valuesDict, error_msg_dict

                    if not radius > 0:
                        error_msg_dict['radius'] = u"Radius must be greater than zero."
                        error_msg_dict['showAlertText'] = u"Radius Error.\n\nRadius must be greater than zero."
                        return False, valuesDict, error_msg_dict

                elif valuesDict['imagetype'] == 'locationbox':
                    if valuesDict['location'].isspace():
                        error_msg_dict['location'] = u"You must specify a valid location. Please see the plugin wiki for examples."
                        error_msg_dict['showAlertText'] = u"Location Error.\n\nYou must specify a valid location. Please see the plugin wiki for examples."
                        return False, valuesDict, error_msg_dict

                return True

            else:

                # Test location setting for devices that must specify one.
                location_config = valuesDict['location']
                if not location_config:
                    error_msg_dict['location'] = u"Please specify a weather location."
                    error_msg_dict['showAlertText'] = u"Location Error.\n\nPlease specify a weather location."
                    return False, valuesDict, error_msg_dict
                elif " " in location_config:
                    error_msg_dict['location'] = u"The location value can't contain spaces."
                    error_msg_dict['showAlertText'] = u"Location Error.\n\nThe location value can not contain spaces."
                    return False, valuesDict, error_msg_dict
                elif "\\" in location_config:
                    error_msg_dict['location'] = u"The location value can't contain a \\ character. Replace it with a / character."
                    error_msg_dict['showAlertText'] = u"Location Error.\n\nThe location value can not contain a \\ character."
                    return False, valuesDict, error_msg_dict
                elif location_config.isspace():
                    error_msg_dict['location'] = u"Please enter a valid location value."
                    error_msg_dict['showAlertText'] = u"Location Error.\n\nPlease enter a valid location value."
                    return False, valuesDict, error_msg_dict

                # Debug output can contain sensitive data.
                if self.pluginPrefs['showDebugLevel'] >= 3:
                    self.debugLog(u"typeID: {0}".format(typeID))
                    self.debugLog(u"devId: {0}".format(devId))
                    self.debugLog(u"============ valuesDict ============\n")
                    for key, value in valuesDict.iteritems():
                        self.debugLog(u"{0}: {1}".format(key, value))
                else:
                    self.debugLog(u"Device preferences suppressed. Set debug level to [High] to write them to the log.")

        except Exception as error:
            self.debugLog(u"Error in validateDeviceConfigUI(). Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        return True

    def validatePrefsConfigUi(self, valuesDict):
        """ Validate select plugin config menu settings. """

        self.debugLog(u"validatePrefsConfigUi() method called.")

        api_key_config      = valuesDict['apiKey']
        call_counter_config = valuesDict['callCounter']
        error_msg_dict      = indigo.Dict()
        update_email        = valuesDict['updaterEmail']
        update_wanted       = valuesDict['updaterEmailsEnabled']

        # Test api_keyconfig setting.
        try:
            if len(api_key_config) == 0:
                # Mouse over text error:
                error_msg_dict['apiKey'] = u"The plugin requires an API key to function. See help for details."
                # Screen error:
                error_msg_dict['showAlertText'] = (u"The API key that you have entered is invalid.\n\n"
                                                   u"Reason: You have not entered a key value. Valid API keys contain alpha-numeric characters only (no spaces.)")
                return False, valuesDict, error_msg_dict

            elif " " in api_key_config:
                error_msg_dict['apiKey'] = u"The API key can't contain a space."
                error_msg_dict['showAlertText'] = (u"The API key that you have entered is invalid.\n\n"
                                                   u"Reason: The key you entered contains a space. Valid API keys contain alpha-numeric characters only.")
                return False, valuesDict, error_msg_dict

            # Test call limit config setting.
            elif not int(call_counter_config):
                error_msg_dict['callCounter'] = u"The call counter can only contain integers."
                error_msg_dict['showAlertText'] = u"The call counter that you have entered is invalid.\n\nReason: Call counters can only contain integers."
                return False, valuesDict, error_msg_dict

            elif call_counter_config < 0:
                error_msg_dict['callCounter'] = u"The call counter value must be a positive integer."
                error_msg_dict['showAlertText'] = u"The call counter that you have entered is invalid.\n\nReason: Call counters must be positive integers."
                return False, valuesDict, error_msg_dict

            # Test plugin update notification settings.
            elif update_wanted and update_email == "":
                error_msg_dict['updaterEmail'] = u"If you want to be notified of updates, you must supply an email address."
                error_msg_dict['showAlertText'] = u"The notification settings that you have entered are invalid.\n\nReason: You must supply a valid notification email address."
                return False, valuesDict, error_msg_dict

            elif update_wanted and "@" not in update_email:
                error_msg_dict['updaterEmail'] = u"Valid email addresses have at least one @ symbol in them (foo@bar.com)."
                error_msg_dict['showAlertText'] = u"The notification settings that you have entered are invalid.\n\nReason: You must supply a valid notification email address."
                return False, valuesDict, error_msg_dict

        except Exception as error:
            self.debugLog(u"Exception in validatePrefsConfigUi API key test. Error: (Line {0}  {1})".format(sys.exc_traceback.tb_lineno, error))

        return True, valuesDict

    def verboseWindNames(self, state_name, val):
        """ The verboseWindNames() method takes possible wind direction values and
        standardizes them across all device types and all reporting stations to
        ensure that we wind up with values that we can recognize. """

        wind_dict = {'N': 'north', 'NNE': 'north northeast', 'NE': 'northeast', 'ENE': 'east northeast', 'E': 'east', 'ESE': 'east southeast', 'SE': 'southeast',
                     'SSE': 'south southeast', 'S': 'south', 'SSW': 'south southwest', 'SW': 'southwest', 'WSW': 'west southwest', 'W': 'west', 'WNW': 'west northwest',
                     'NW': 'northwest', 'NNW': 'north northwest'}

        if self.debug and self.pluginPrefs['showDebugLevel'] >= 3:
            self.debugLog(u"verboseWindNames(self, state_name={0}, val={1}, verbose={2})".format(state_name, val, wind_dict[val]))

        try:
            return wind_dict[val]
        except KeyError:
            return val

