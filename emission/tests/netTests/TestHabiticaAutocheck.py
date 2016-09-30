# Standard imports
import logging
import random
import unittest
import uuid

import arrow
import attrdict as ad
import dateutil.tz as tz

import emission.analysis.result.metrics.simple_metrics as earmts
import emission.analysis.result.metrics.time_grouping as earmt
import emission.core.get_database as edb
import emission.core.wrapper.entry as ecwe
import emission.core.wrapper.motionactivity as ecwm
import emission.core.wrapper.section as ecws
import emission.net.ext_service.habitica.proxy as proxy
import emission.storage.decorations.analysis_timeseries_queries as esda
import emission.storage.decorations.local_date_queries as esdl
import emission.storage.timeseries.abstract_timeseries as esta
import net.ext_service.habitica.auto_tasks.active_distance as autocheck

PST = "America/Los_Angeles"


class TestHabiticaRegister(unittest.TestCase):
  def setUp(self):
    #load test user
    self.testUUID = uuid.uuid4()
    autogen_string = randomGen()
    autogen_email = autogen_string + '@test.com'
    self.sampleAuthMessage1 = {'username': autogen_string, 'email': autogen_email, 
      'password': "test", 'our_uuid': self.testUUID}
    sampleAuthMessage1Ad = ad.AttrDict(self.sampleAuthMessage1)
    proxy.habiticaRegister(sampleAuthMessage1Ad.username, sampleAuthMessage1Ad.email,
                           sampleAuthMessage1Ad.password, sampleAuthMessage1Ad.our_uuid)
    edb.get_habitica_db().update({"user_id": self.testUUID},{"$set": {'metrics_data.last_timestamp': arrow.Arrow(2016,5,1).timestamp}},upsert=True)

    self.ts = esta.TimeSeries.get_time_series(self.testUUID)
    bike_habit = {'type': "habit", 'text': "Bike", 'up': True, 'down': False, 'priority': 2}
    bike_habit_id = proxy.create_habit(self.testUUID, bike_habit)
    walk_habit = {'type': "habit", 'text': "Walk", 'up': True, 'down': False, 'priority': 2}
    walk_habit_id = proxy.create_habit(self.testUUID, walk_habit)
    logging.debug("in setUp, result = %s" % self.ts)


  def tearDown(self):
    edb.get_analysis_timeseries_db().remove({'user_id': self.testUUID})
    del_result = proxy.habiticaProxy(self.testUUID, "DELETE",
                                     "/api/v3/user",
                                     {'password': "test"})
    edb.get_habitica_db().remove({'user_id': self.testUUID})
    logging.debug("in tearDown, result = %s" % del_result)


  def testCreateExistingHabit(self):
    #try to create Bike
    existing_habit = {'type': "habit", 'text': "Bike"}
    habit_id = proxy.create_habit(self.testUUID, existing_habit)
    logging.debug("in testCreateExistingHabit, the new habit id is = %s" % habit_id)
    #search this user's habits for the habit and check if there's exactly one
    response = proxy.habiticaProxy(self.testUUID, 'GET', "/api/v3/tasks/user?type=habits", None)
    logging.debug("in testCreateExistingHabit, GET habits response = %s" % response)
    habits = response.json()
    logging.debug("in testCreateExistingHabit, this user's list of habits = %s" % habits)
    self.assertTrue(habit['_id'] == habit_id for habit in habits['data'])
    self.assertTrue(habit['text'] == new_habit['text'] for habit in habits['data'])
    #search this user's habits for the habit and check if there's exactly one
    occurrences = (1 for habit in habits['data'] if habit['text'] == existing_habit['text'])
    self.assertEqual(sum(occurrences), 1)


  def testCreateNewHabit(self):
    new_habit = {'type': "habit", 'text': randomGen()}
    habit_id = proxy.create_habit(self.testUUID, new_habit)
    logging.debug("in testCreateNewHabit, the new habit id is = %s" % habit_id)
    #Get user's list of habits and check that new habit is there
    response = proxy.habiticaProxy(self.testUUID, 'GET', "/api/v3/tasks/user?type=habits", None)
    logging.debug("in testCreateNewHabit, GET habits response = %s" % response)
    habits = response.json()
    logging.debug("in testCreateNewHabit, this user's list of habits = %s" % habits)
    self.assertTrue(habit['_id'] == habit_id for habit in habits['data'])
    self.assertTrue(habit['text'] == new_habit['text'] for habit in habits['data'])
    

  def _createTestSection(self, start_ardt, start_timezone):
    section = ecws.Section()
    self._fillDates(section, "start_", start_ardt, start_timezone)
    # Hackily fill in the end with the same values as the start
    # so that the field exists
    # in cases where the end is important (mainly for range timezone
    # calculation with local times), it can be overridden using _fillDates
    # from the test case
    self._fillDates(section, "end_", start_ardt, start_timezone)
    #logging.debug("created section %s" % (section.start_fmt_time))
    entry = ecwe.Entry.create_entry(self.testUUID, esda.CLEANED_SECTION_KEY,
                                    section, create_id=True)
    self.ts.insert(entry)
    return entry

  def _fillDates(self, object, prefix, ardt, timezone):
    object["%sts" % prefix] = ardt.timestamp
    object["%slocal_dt" % prefix] = esdl.get_local_date(ardt.timestamp, timezone)
    object["%sfmt_time" % prefix] = ardt.to(timezone).isoformat()
    #logging.debug("After filling entries, keys are %s" % object.keys())
    return object

  def _fillModeDistanceDuration(self, section_list):
    for i, s in enumerate(section_list):
      dw = s.data
      dw.sensed_mode = ecwm.MotionTypes.BICYCLING
      dw.duration = (i + 1) * 100
      dw.distance = (i + 1.5) * 1000
      s['data'] = dw
      self.ts.update(s)


  def testAutomaticRewardActiveTransportation(self):
    #Create test data -- code copied from TestTimeGrouping
    key = (2016, 5, 3)
    test_section_list = []
    #
    # Since PST is UTC-7, all of these will be in the same UTC day
    # 13:00, 17:00, 21:00
    # so we expect the local date and UTC bins to be the same
    test_section_list.append(
        self._createTestSection(arrow.Arrow(2016,5,3,6, tzinfo=tz.gettz(PST)),
                                PST))
    test_section_list.append(
        self._createTestSection(arrow.Arrow(2016,5,3,10, tzinfo=tz.gettz(PST)),
                                PST))
    test_section_list.append(
        self._createTestSection(arrow.Arrow(2016,5,3,14, tzinfo=tz.gettz(PST)),
                                PST))

    self._fillModeDistanceDuration(test_section_list)
    #logging.debug("durations = %s" % [s.data.duration for s in test_section_list])

    summary_ts = earmt.group_by_timestamp(self.testUUID,
                                       arrow.Arrow(2016,5,1).timestamp,
                                       arrow.Arrow(2016,6,1).timestamp,
                                       None, earmts.get_distance)
    logging.debug("in testAutomaticRewardActiveTransportation, result = %s" % summary_ts)
    
    #Get user data before scoring
    user_before = list(edb.get_habitica_db().find({'user_id': self.testUUID}))[0]['metrics_data']
    self.assertEqual(int(user_before['bike_count']),0)
    habits_before = proxy.habiticaProxy(self.testUUID, 'GET', "/api/v3/tasks/user?type=habits", None).json()
    bike_pts_before = [habit['history'] for habit in habits_before['data'] if habit['text'] == "Bike"]
    #Score points
    autocheck.reward_active_transportation(self.testUUID)
    #Get user data after scoring and check results
    user_after = list(edb.get_habitica_db().find({'user_id': self.testUUID}))[0]['metrics_data']
    self.assertEqual(int(user_after['bike_count']),1500)
    habits_after = proxy.habiticaProxy(self.testUUID, 'GET', "/api/v3/tasks/user?type=habits", None).json()
    bike_pts_after = [habit['history'] for habit in habits_after['data'] if habit['text'] == "Bike"]
    self.assertTrue(len(bike_pts_after[0]) - len(bike_pts_before[0]) == 2)



def randomGen():
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    length = 5
    string = ""
    for i in range(length):
      next_index = random.randrange(len(alphabet))
      string = string + alphabet[next_index]
    return string


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
