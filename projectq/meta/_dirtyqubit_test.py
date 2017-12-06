#   Copyright 2017 ProjectQ-Framework (www.projectq.ch)
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""Tests for projectq.meta._dirtyqubit.py"""

import pytest

from projectq import MainEngine
from projectq.cengines import DummyEngine, DirtyQubitMapper
from projectq.backends import ResourceCounter
from projectq.ops import X, H, CNOT, Toffoli, HGate, AllocateQubitGate
from projectq.meta import (DirtyQubitTag,
                           ComputeTag,
                           DirtyQubits,
                           DirtyQubitManagementError)



@pytest.fixture
def dqubitsection_testengine():
    #  DirtyQubitMapper in enginelist so that DirtyQubitTag is supported
    return MainEngine(DummyEngine(save_commands=True),
                      [DummyEngine(save_commands=True), DirtyQubitMapper()])


def test_dirty_qubit_tag():
    tag0 = DirtyQubitTag()
    tag1 = DirtyQubitTag()
    tag2 = ComputeTag()
    assert tag0 == tag1
    assert not tag0 != tag1
    assert not tag0 == tag2


def test_tags_added_dqubitsection(dqubitsection_testengine):
    """
    Tests whether the correct tags are added by the DirtyQubits-Section
    """
    dummy = dqubitsection_testengine.next_engine  # before dqubit gets remapped

    qubit = dqubitsection_testengine.allocate_qubit()

    with DirtyQubits(dqubitsection_testengine, qubit):
        dqubit = dqubitsection_testengine.allocate_qubit(dirty=True)
        del dqubit
        
    assert any(isinstance(tag, DirtyQubitTag)
               for tag in dummy.received_commands[-1].tags), (
           "DirtyQubitTag was not added to DeallocateQubit command," +
           " problem in BasicEngine?")

    assert any(isinstance(tag, DirtyQubitTag)
               for tag in dummy.received_commands[-2].tags), (
           "DirtyQubitTag was not added to AllocateQubit command," +
           " problem in BasicEngine?")
    assert dummy.received_commands[-2].tags[0].target_IDs == {0}, (
           "The target ID is not 0")


def test_error_missing_deallocate_dqubitsection(dqubitsection_testengine):
    """
    Tests if an error is raised when a dirty qubit allocated in a DirtyQubits-
    section is not deallocated
    """
    qubit = dqubitsection_testengine.allocate_qubit()
    try:
        with DirtyQubits(dqubitsection_testengine,qubit):
            qubit2 = dqubitsection_testengine.allocate_qubit()
    except DirtyQubitManagementError:
        assert False, "Error raised on missing deallocation of clean qubit"

    try:
        with DirtyQubits(dqubitsection_testengine,qubit):
            dqubit = dqubitsection_testengine.allocate_qubit(dirty=True)
    except DirtyQubitManagementError:
        return
    assert False, "No error raised on missing deallocation of dirty qubit"


if __name__ == '__main__':
    test_error_missing_deallocate_dqubitsection(dqubitsection_testengine())


