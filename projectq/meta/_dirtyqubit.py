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

"""
Contains the tools to indicate the preferred carriers (qubits which dirty
qubits get mapped into) during a 'with CarrierQubits'-section to the
DirtyQubitMapper.
Also defines the DirtyQubitTag meta tag
"""

from projectq.cengines import BasicEngine
from projectq.types import BasicQubit
from projectq.ops import AllocateQubitGate, DeallocateQubitGate
from ._util import insert_engine, drop_engine_after


class DirtyQubitManagementError(Exception):
    pass


class DirtyQubitTag(object):
    """
    Dirty qubit meta tag
    Contains a list of carriers, which holds the ids of target qubits.
    These carriers are the preferred qubits to map the dirty qubit into
    """
    def __init__(self, carriers=[]):
        """
        carriers (list<int>): IDs of qubits that the dirty qubit
        preferably gets mapped into
        """
        self.carrier_IDs = set(carriers)

    def __eq__(self, other):
        # the second part of the expression gets evaluated conditionally,
        # ie only if both objects are DirtyQubitTags their carrier sets are
        # compared
        return (isinstance(other, DirtyQubitTag) and
                self.carrier_IDs == other.carrier_IDs)

    def __ne__(self, other):
        return not self.__eq__(other)


class CarrierIndicator(BasicEngine):
    """
    Indicates for each dirty qubit allocation which the carriers for that dirty
    qubit are (which qubits the dqubit should get mapped into).
    Does so by adding the IDs of the carriers to the carrier-list in the
    DirtyQubitTag of QubitAllocationGates
    """
    def __init__(self, carrier_qubits):
        self._carrier_IDs = set([qb.id for qb in carrier_qubits])
        self._active_dqubits = set()

    def receive(self, cmd_list):
        for cmd in cmd_list:
            if isinstance(cmd.gate, AllocateQubitGate) and \
               any(isinstance(tag, DirtyQubitTag) for tag in cmd.tags):
                for tag in cmd.tags:
                    if isinstance(tag, DirtyQubitTag):
                        tag.carrier_IDs.update(self._carrier_IDs)
                self._active_dqubits.add(cmd.qubits[0][0].id)
            elif isinstance(cmd.gate, DeallocateQubitGate):
                    self._active_dqubits.discard(cmd.qubits[0][0].id)
            self.send([cmd])

    def end_targetting(self):
        if self._active_dqubits:
            raise DirtyQubitManagementError(
                "A dirty qubit allocated in this 'with DirtyQubits'-section " +
                "has not been deallocated within the section")


class CarrierQubits(object):
    """
    Indicate to the DirtyQubitMapper the preferred carriers (qubits to map
    dirty qubits into) during a section.

    Example:
        .. code-block:: python

            with DirtyQubits(eng, carriers):
                dirty_qubit = eng.allocate_qubit(dirty = True)
                ...
                del dirty_qubit
                # dirty_qubit will be mapped into carriers (if possible)

    Warning:
            Dirty qubits allocated in a CarrierQubits-section have to be
            deallocated again in the same section.
    """

    def __init__(self, engine, qubits):
        """
        Enter a DirtyQubits section

        Args:
            engine: Engine which handles the commands (usually MainEngine)
            qubits (list of Qubit objects): Qubits to map dirty qubits into

        Enter the section using a with-statement:

        .. code-block:: python

            with DirtyQubits(eng, carriers):
                ...
        """
        self.engine = engine
        assert(not isinstance(qubits, tuple))
        if isinstance(qubits, BasicQubit):
            qubits = [qubits]
        self._carriers = qubits

    def __enter__(self):
        if len(self._carriers) > 0:
            self._targeter = CarrierIndicator(self._carriers)
            insert_engine(self.engine, self._targeter)

    def __exit__(self, type, value, traceback):
        # remove control handler from engine list (i.e. skip it)
        if len(self._carriers) > 0:
            self._targeter.end_targetting()
            drop_engine_after(self.engine)
