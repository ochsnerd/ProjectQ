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
Contains the tools to indicate the preferred targets (qubits which dirty qubits
get mapped into) during a 'with DirtyQubits'-section to the DirtyQubitMapper.
Also defines the DirtyQubitTag meta tag
"""

from projectq.cengines import BasicEngine
from projectq.types import BasicQubit
from projectq.ops import AllocateQubitGate, DeallocateQubitGate
from ._util import insert_engine, drop_engine_after


class DirtyQubitManagementError(Exception):
    pass


# This tag could be integrated into DirtyQubitTag
# makes checking for a DirtyQubitTag slightly more bothersome:
# any(isinstance(tag, TargetQubitTag) for tag in cmd.tags):
# instead of
# DirtyQubitTag() in cmd.tags
class TargetQubitTag(object):
    """
    Target meta tag to indicate where dirty qubits are preferrably mapped to
    Gets attached to dirty allocation gates
    """
    def __init__(self, IDs):
        """
        IDs (list of ints): IDs of qubits that the dirty qubit preferably gets
        mapped into
        """
        self.IDs = IDs

    def __eq__(self, other):
        return isinstance(other, TargetQubitTag)

    def __ne__(self, other):
        return not self.__eq__(other)


class DirtyQubitTag(object):
    """
    Dirty qubit meta tag
    Contains a list of targets, which holds the ids of target qubits.
    These targets are the preferred qubits to map the dirty qubit into
    """
    def __init__(self, targets=[]):
        """
        targets (list of ints): IDs of qubits that the dirty qubit
        preferably gets mapped into
        """
        self.target_IDs = targets

    def __eq__(self, other):
        # the second part of the expression gets evaluated conditionally,
        # ie only if both objects are DirtyQubitTags their target lists are
        # compared
        return (isinstance(other, DirtyQubitTag) and
                sorted(self.target_IDs) == sorted(other.target_IDs))

    def __ne__(self, other):
        return not self.__eq__(other)


class TargetIndicator(BasicEngine):
    """
    Indicates for each dirty qubit allocation which the targets for that dirty
    qubit are (which qubits the dqubit should get mapped into).
    Does so by adding the IDs of the targets to the target list in the
    DirtyQubitTag of QubitAllocationGates
    """
    def __init__(self, target_qubits):
        self._target_IDs = [qb.id for qb in target_qubits]
        self._active_dqubits = []

    def receive(self, cmd_list):
        for cmd in cmd_list:
            if isinstance(cmd.gate, AllocateQubitGate) and \
               any(isinstance(tag, DirtyQubitTag) for tag in cmd.tags):
                for tag in cmd.tags:
                    if isinstance(tag, DirtyQubitTag):

                        print("Targets before adding: " + str(tag.target_IDs))

                        tag.target_IDs.extend(self._target_IDs)

                        print("Targets after adding: " + str(tag.target_IDs))

                self._active_dqubits.append(cmd.qubits[0][0].id)
            elif isinstance(cmd.gate, DeallocateQubitGate):
                try:
                    self._active_dqubits.remove(cmd.qubits[0][0].id)
                except ValueError:
                    # deallocated qubit was not dirty or
                    # not allocated in this section
                    pass
            self.send([cmd])

    def end_targetting(self):
        if self._active_dqubits != []:
            raise DirtyQubitManagementError(
                "A dirty qubit allocated in this 'with DirtyQubits'-section " +
                "has not been deallocated within the section")


class DirtyQubits(object):
    """
    Indicate to the DirtyQubitMapper the preferred targets (qubits to map dirty
    qubits into) during a section.

    Example:
        .. code-block:: python

            with DirtyQubits(eng, targets):
                dirty_qubit = eng.allocate_qubit(dirty = True)
                do_stuff()
                eng.deallocate_qubit(dirty_qubit[0])
                # dirty_qubit will be mapped into targets (if possible)
    """

    def __init__(self, engine, qubits):
        """
        Enter a DirtyQubits section

        Args:
            engine: Engine which handles the commands (usually MainEngine)
            qubits (list of Qubit objects): Qubits to map dirty qubits into

        Enter the section using a with-statement:

        .. code-block:: python

            with DirtyQubits(eng, targets):
                ...
        """
        self.engine = engine
        assert(not isinstance(qubits, tuple))
        if isinstance(qubits, BasicQubit):
            qubits = [qubits]
        self._targets = qubits
        #  CHECK THAT DIRTYQUBITTAG IS SUPPOERTED, POINTLESS OTHERWISE

    def __enter__(self):
        if len(self._targets) > 0:
            self._targeter = TargetIndicator(self._targets)
            insert_engine(self.engine, self._targeter)

    def __exit__(self, type, value, traceback):
        self._targeter.end_targetting()
        # remove control handler from engine list (i.e. skip it)
        if len(self._targets) > 0:
            drop_engine_after(self.engine)
