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

# David Ochsner
# 2017-10-18
# ochsnerd@student.ethz.ch
import pdb

from projectq.cengines import BasicEngine
from projectq.meta import DirtyQubitTag, TargetQubitTag
from projectq.ops import (AllocateQubitGate,
                          AllocateDirtyQubitGate,
                          DeallocateQubitGate,
                          FlushGate,
                          FastForwardingGate)


class DirtyQubitMapper(BasicEngine):
    def __init__(self, verbose=False):
        BasicEngine.__init__(self)

        # List of lists, one list per active qubit. Commands (gates) acting on
        # dirty qubits or qubits interacting with dirty qubits are cached
        # The position in the list corresponds to the qubit ID.
        # If the qubit is 'invalid' (it has not been allocated yet or it was
        # already deallocated), the list is just [-1]
        self._cached_cmds = []  # becomes list of lists

        self._verbose = verbose
        self.manualmap = -1

    def is_meta_tag_handler(self, tag):
        if tag in [DirtyQubitTag, TargetQubitTag]:
            return True
        else:
            return False

    def is_available(self, cmd):
        return True

    def _send_qubit_pipeline(self, snd_ID, n):
        """
        Sends out n cached commands acting in the snd_ID-qubit
        """
        cmds = self._cached_cmds[snd_ID]  # for readability
        if cmds == [-1]:
            # attempting to send our 'invalid qubit' flag
            self._print("Trying to send our invalid qubit flag")
            return

        self._print("Sending on " + str(n) +
                    " commands on qubit " + str(snd_ID))
        sent_deallocate = False  # to be able to later invalidate snd_ID

        for i in range(min(n, len(cmds))):
            other_involved_qubits = [qb
                                     for qreg in cmds[i].all_qubits
                                     for qb in qreg
                                     if qb.id != snd_ID]

            for qb in other_involved_qubits:
                # index = self._find_index(qb.id)
                ID = qb.id
                try:
                    # find position of the cmd in the list of the other qubit
                    cmd_pos = 0
                    while self._cached_cmds[ID][cmd_pos] != cmds[i]:
                        cmd_pos += 1

                    # send all commands acting on the other qubit which were
                    # cached before this cmd
                    self._send_qubit_pipeline(ID, cmd_pos)

                    # all previous commands on the other qubit were sent on,
                    # we delete the one we're inspecting now in the other list
                    # to avoid sending it multiple times
                    self._cached_cmds[ID] = self._cached_cmds[ID][1:]
                except IndexError:
                    self._print("Invalid qubit pipeline encountered (in the" +
                                " process of shutting down?).")
            # all commands interacting with our current one have been sent on
            # we can send the current one
            self.send([cmds[i]])
            if isinstance(cmds[i].gate, DeallocateQubitGate):
                sent_deallocate = True

        # remove the cmds we just sent on from _cached_cmds
        self._cached_cmds[snd_ID] = self._cached_cmds[snd_ID][n:]

        if sent_deallocate:
            # invalidate ID if we sent the deallocation
            assert self._cached_cmds[snd_ID] == []
            self._cached_cmds[snd_ID] = [-1]

    def _find_target(self, rmp_ID):
        """
        Finds a valid (i.e. not interacting with the dirty qubit) qubit
        (target) to map the qubit with rmp_ID into.
        If no such qubit is found, returns None
            -prefer qubits indicated by 'with DirtyQubits'
        """
        if self.manualmap != -1:
            return self.manualmap

        def check_involvement(check_ID, n, ID_list):
            """
            Deletes IDs out of the ID_list if they are involved with the first
            n commands on qubit check_ID
            """
            self._print("-------------------------")
            self._print("New call, looking at ID" + str(check_ID) +
                        ", first " + str(n) + " cmds")
            self._print(ID_list)
            for i, cmd in enumerate(reversed(self._cached_cmds[check_ID][:n])):
                self._print(cmd)
                listpos = min(n, len(self._cached_cmds[check_ID])) - i - 1
                cmd_pos, other_involved_IDs = get_cmd_pos(check_ID, listpos)
                for ID in other_involved_IDs:
                    ID_list = check_involvement(ID, listpos, ID_list)
                    if ID in ID_list:
                        self._print("Found involvement with " + str(ID))
                        ID_list.remove(ID)
            return ID_list

        def get_cmd_pos(ID, i):
            """
            Return a list of indices of the command at the i-th postion in ID's
            cached commands, aswell as a list of all qubits IDs involved in
            that command.
            EXCLUDES THE ID ITSELF (in both lists)

            Args:
                ID (int): qubit index
                i (int): command position in qubit ID's command list
            """
            other_IDs = [qb.id
                         for qureg in self._cached_cmds[ID][i].all_qubits
                         for qb in qureg
                         if qb.id != ID]
            # 1-qubit gate: only gate at index i is involved
            if other_IDs:
                return [], other_IDs

            # When the same gate appears multiple times, we need to make sure
            # not to match earlier instances of the gate applied to the same
            # qubits. So we count how many there are, and skip over them when
            # looking in the other lists.
            cmd = self._cached_cmds[ID][i]
            n_identical_to_skip = sum(1
                                      for prv_cmd in self._cached_cmds[ID][:i]
                                      if prv_cmd == cmd)
            indices = []
            for Id in other_IDs:
                ident_indices = [i
                                 for i, c in enumerate(self._cached_cmds[ID])
                                 if c == cmd]
                indices.append(ident_indices[n_identical_to_skip])
            return indices, other_IDs

        preferred_qubits = []
        # check if we have a list of preferred targets for this dirty qubit
        for tag in self._cached_cmds[rmp_ID][0].tags:
            if isinstance(tag, TargetQubitTag):
                preferred_qubits += tag.IDs

        if preferred_qubits != []:
            # we found preferred targets
            preferred_qubits = check_involvement(rmp_ID,
                                                 len(self._cached_cmds[rmp_ID]),
                                                 preferred_qubits)
            if preferred_qubits != []:
                # some of the preferred targets can be mapped into
                self._print("Found preferred, possible qubits: "
                            + str(preferred_qubits))
                self._print("Remapping into " + str(preferred_qubits[0]))
                return preferred_qubits[0]

        possible_qubits = [i for i, cmds
                           in enumerate(self._cached_cmds)
                           if cmds != [-1]]
        possible_qubits.remove(rmp_ID)

        possible_qubits = check_involvement(rmp_ID,
                                            len(self._cached_cmds[rmp_ID]),
                                            possible_qubits)

        self._print("Found qubits that we can map into: " +
                    str(possible_qubits))

        if possible_qubits == []:
            return None

        self._print("Remapping into " + str(possible_qubits[0]))

        return possible_qubits[0]

    def _remap_dqubit(self, rmp_ID):
        """
        Remaps the operations on deallocated dirty qubit to a qubit not
        interacting with that particular qubit (target), if such a qubit
        exists. Returns the ID of the target, plus whether the target was
        involved beforehand, ie whether the commands have to stay cached or
        can be sent on
        """
        self._print("Remapping deallocated dqubit")

        assert self._is_dirty_alloc(self._cached_cmds[rmp_ID][0])

        # No gates performed on dqubit other than allocate/deallocate
        # we just have to clean up
        if self._cached_cmds[rmp_ID][1:-1] == []:
            self._print("Don't have to remap 'empty' dqubit")
            self._cached_cmds[rmp_ID] = [-1]
            return rmp_ID, True  # don't send on our invalid flag

        self._print("####################################################")
        new_ID = self._find_target(rmp_ID)
        self._print("####################################################")
        # maybe there is no possible qubit to remap to
        if new_ID is None:
            return rmp_ID, False

        # remove allocate and deallocate command
        self._cached_cmds[rmp_ID] = self._cached_cmds[rmp_ID][1:-1]

        # Change ID of qubits of cached commands
        for cmd in self._cached_cmds[rmp_ID]:
            for sublist in cmd.all_qubits:
                for qubit in sublist:
                    if qubit.id == rmp_ID:
                        qubit.id = new_ID

        wait = True  # set later

        # append commands acting on qubit rmp_ID to list of qubit new_ID
        if self._cached_cmds[new_ID] == []:
            self._print("Map to not involved")
            self._cached_cmds[new_ID] = self._cached_cmds[rmp_ID]
            self._cached_cmds[rmp_ID] = [-1]
            wait = False
        else:
            self._print("Map to involved")
            if isinstance(self._cached_cmds[new_ID][-1], DeallocateQubitGate):
                # The qubit we map to is already deallocated, but still cached
                # sneak in the cmds of the dqubit before the deallocation
                self._cached_cmds[new_ID] = (self._cached_cmds[new_ID][:-1]
                                             + self._cached_cmds[rmp_ID]
                                             + self._cached_cmds[new_ID][-1])
            else:
                self._cached_cmds[new_ID].extend(self._cached_cmds[rmp_ID])
            self._cached_cmds[rmp_ID] = [-1]
            wait = True

        self._print("After remapping dqubit")
        self.print_state()

        return new_ID, wait

    def _check_and_send(self):
        """
        Checks if the last cmds in _cached_cmds are DeallocateQubitGate
        acting on dirty qubits. If so, they are remapped - if possible -
        and then sent to the next engine
        """
        self._print("Called checkandsend")
        for ID, cmd_list in enumerate(self._cached_cmds):
            if cmd_list == [] or cmd_list == [-1]:
                # no cached cmds or the qubit has already been deallocated
                # or it is not yet allocated - either way we don't do anything
                continue

            if self._is_dirty_dealloc(cmd_list[-1]):
                wait = False
                mappedinto_ID = ID
                if self._is_dirty_alloc(cmd_list[0]):
                    self._print("Trying to remap " + str(ID))
                    # a dirty qubit was deallocated and we have it's whole
                    # lifetime cached - we can try to remap it!
                    mappedinto_ID, wait = self._remap_dqubit(ID)

                # if we mapped into an involved qubit, we wait.
                # else we send on the cmds
                if not wait:
                    n = len(self._cached_cmds[mappedinto_ID])
                    self._send_qubit_pipeline(mappedinto_ID, n)

    def _cache_cmd(self, cmd):
        """
        Caches a command (adds a copy of the command to each list the qubits it
        acts on)
        """
        id_list = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]
        for ID in id_list:
            self._cached_cmds[ID].append(cmd)

    def _is_involved(self, cmd):
        """
        Checks if cmd acts on an involved qubit
        """
        id_list = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]
        for ID in id_list:
            if self._cached_cmds[ID] != [] and \
               self._cached_cmds[ID] != [-1]:
                return True
        return False

    def _is_dirty_alloc(self, cmd):
        """
        Checks if cmd is allocation of a dirty qubit
        """
        if DirtyQubitTag() in cmd.tags \
           and isinstance(cmd.gate, AllocateQubitGate):
            return True
        return False
        
    def _is_dirty_dealloc(self, cmd):
        """
        Checks if cmd is allocation of a dirty qubit
        """
        if DirtyQubitTag() in cmd.tags \
           and isinstance(cmd.gate, DeallocateQubitGate):
            return True
        return False

    def receive(self, command_list):
        """
        Receive list of commands from previous compiler engines.
        Commands are sent on unless they interact with a dirty qubit
        """
        for cmd in command_list:
            self._print("Inspecting command:")
            self._print(cmd)

            # updating _cached_cmds
            if isinstance(cmd.gate, AllocateQubitGate):
                new_ID = cmd.qubits[0][0].id
                self._print("Adding qubit " + str(new_ID) + " to cached_cmds")
                self._print(len(self._cached_cmds))
                if len(self._cached_cmds) == new_ID:
                    # the qubit is allocated with the consecutive ID
                    self._cached_cmds.append([])
                elif len(self._cached_cmds) > new_ID:
                    # the allocation gate got delayed in a previous cengine
                    assert self._cached_cmds[new_ID] == [-1]
                    self._cached_cmds[new_ID] = []
                else:
                    # other allocation gates got delayed in previous cengines -
                    # have to make sure the list indices match up with IDs
                    skipped = new_ID - len(self._cached_cmds)
                    self._cached_cmds.extend([[-1] for _ in range(skipped)])
                    self._cached_cmds.append([])

            if isinstance(cmd.gate, FlushGate):
                # received flush-gate - send on all cached cmds
                self._print("Received FlushGate")
                for ID, cmd_list in enumerate(self._cached_cmds):
                    self._send_qubit_pipeline(ID, len(cmd_list))
            elif self._is_involved(cmd) or self._is_dirty_alloc(cmd):
                # received command involving a qubit already involved or an
                # allocation of a dirty qubit - cache the command
                self._print("Caching")
                self._cache_cmd(cmd)
                self._check_and_send()
            else:
                # the received command doesn't concern us, we update our list
                # and then send it on
                self._print("Forwarding")
                if isinstance(cmd.gate, DeallocateQubitGate):
                    self._print("Invalidating qubit ID")
                    dealloc_ID = cmd.qubits[0][0].id
                    self._cached_cmds[dealloc_ID] = [-1]
                self.send([cmd])
        self.print_state()
        self._print("\n\n")

    """
    Helpers for "debugging"
    """
    def _print(self, message):
        if self._verbose:
            print(message)

    def print_state(self):
        if not self._verbose:
            return
        print("----------------------------------")
        print("State of dirtymapper:")
        for qubit_id, cmds in enumerate(self._cached_cmds):
            print("  Acting on qubit " + str(qubit_id))
            for cmd in cmds:
                print("    " + str(cmd))
        print("----------------------------------")
