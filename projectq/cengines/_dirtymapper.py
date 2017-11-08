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



from projectq.cengines import BasicEngine
from projectq.meta import DirtyQubitTag
from projectq.ops import   (AllocateQubitGate,
                            AllocateDirtyQubitGate,
                            DeallocateQubitGate,
                            FlushGate,      # can I get them from here?
                            FastForwardingGate)

class DirtyQubitMapper(BasicEngine):
    def __init__(self):
        BasicEngine.__init__(self)
        # Question: What about main_engine.dirty_qubits ? Is this in use?
        # I'm guessing not because DeallocateQubitGate doesn't get the DirtyQubitTag
        # (see cengines/_basics.py: ln149)
        # list-index is the same for a given qubit-index
        # in both _involved_qubits and _cached_cmds
        self._involved_qubits = []
        self._cached_cmds = []

    def is_meta_tag_handler(self, tag):
        if tag == DirtyQubitTag:
            return True
        else:
            return False

    def is_available(self, cmd):
        return True
        
    def _find_remap_qubitID(self, rmp_ID):
        """
        Finds a valid (i.e. not interacting with the dirty qubit) qubit to map
        the qubit with rmp_ID into. If no such qubit is found, returns None
            -find non-interacting qubit in _involved_qubits (not very likely)
            -obtain ID of allocated, not involved qubit (easy if
                ID < biggest_involved_id, difficult otherwise?)
            -prefer qubits indicated by with DirtyQubits
        """
        return None
        
    def _remap_dqubit(self, rmp_ID):
        """
        Remaps the operations on deallocated dirty qubit to a qubit not
        interacting with that particular qubit, if such a qubit exists.
        Returns the ID of the qubit it mapped the dirty qubit into
        """
        print("Remapping deallocated dqubit")
        assert(rmp_ID in self._involved_qubits)
        
        # find index of dqubit in our list
        for i,ID in enumerate(self._involved_qubits):
                if ID == rmp_ID:
                    rmp_index = i
                    
        assert(self._is_dirty_alloc(self._cached_cmds[rmp_index][0]))
        
        new_ID = self._find_remap_qubitID(rmp_ID)
        # maybe there is no possible qubit to remap to
        if not new_ID == None:
            # remove allocate and deallocate command
            self._cached_cmds[rmp_index] = self._cached_cmds[rmp_index][1:-1]
            
            # Change ID of qubits of cached commands
            for cmd in self._cached_cmds[rmp_index]:
                for sublist in cmd.all_qubits:
                    for qubit in sublist:
                        if qubit.id == rmp_ID:
                            qubit.id = new_ID
            
            # append commands acting on qubit rmp_ID to list of qubit new_ID
            if not new_ID in self._involved_qubits:
                # the qubit we map our dqubit into is not yet involved - we can
                # just change labels
                print("Map not involved")
                self._involved_qubits[rmp_index] = new_ID
                # No gates performed on dqubit other than allocate/deallocate
                if self._cached_cmds[rmp_index] == []:
                    del self._involved_qubits[rmp_index]
                    del self._cached_cmds[rmp_index]
            else:
                print("Map involved")
                for i,ID in enumerate(self._involved_qubits):
                    if ID == new_ID:
                        self._cached_cmds[i].extend(self._cached_cmds[rmp_index])
                        del self._involved_qubits[rmp_index]
                        del self._cached_cmds[rmp_index]

        print("After remapping dqubit")
        self.print_state()
        
        return new_ID

    def _check_and_send(self):
        """
        Checks if the last cmds in _cached_cmds are either FastForwardingGate or
        DeallocateQubitGate acting on dirty qubits. If so, they are remapped if
        possible and then sent to the next engine
        """
        print("Called checkandsend")
        for i,ID in enumerate(self._involved_qubits):
            # Question: Is just looking at the last gate correct? This would not
            # work if you apply an ordinary gate right after measuring?
            last_cmd = self._cached_cmds[i][-1] # for readability
            assert(ID in [qubit.id
                          for sublist in last_cmd.all_qubits
                          for qubit in sublist])
            if (self._is_dirty_alloc(self._cached_cmds[i][0]) and
                isinstance(last_cmd.gate, DeallocateQubitGate)):
                print("Dirty deallocate detected")
                mappedinto_ID = self._remap_dqubit(ID)
                # self._send_qubit_pipeline(mappedinto_ID)
            elif isinstance(last_cmd.gate, FastForwardingGate):
                # Question: what about measurements on dqubits?
                # They should be fastforwarded to be accessible, but then I have
                # a half-remapped dqubit for which I have to:
                # - save the ID where I mapped it to
                # - make sure everything from that ID gets cached until the dqubit
                #   is deallocated
                # Question: what about measurements on clean qubits involved with
                # dirty qubits? Send their cached commands on? What about commands
                # involving dirtyqubits?
                print("FastForwardingGate detected: at ID " + str(ID))
                # self._send_qubit_pipeline(ID)

    def _cache_cmd(self, cmd):
        """
        Caches a command (adds it to its respective list)
        after checking if new qubits are involved and updating the _involved_qubits
        """
        id_list = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]
        for ID in id_list:
            if not ID in self._involved_qubits:
                self._involved_qubits.append(ID)
                self._cached_cmds.append([])
            self._cached_cmds[self._involved_qubits.index(ID)].append(cmd)
            
        self.print_state()

    def _is_involved(self, cmd):
        """
        Checks if cmd acts on an involved qubit
        """
        id_list = [qubit.id for sublist in cmd.all_qubits for qubit in sublist]
        for ID in id_list:
            if ID in self._involved_qubits:
                return True
        return False

    def _is_dirty_alloc(self, cmd):
        """
        Checks if cmd is allocation of a dirty qubit
        """
        if (DirtyQubitTag() in cmd.tags
            and isinstance(cmd.gate, AllocateQubitGate) # Question: Doing this instead of cmd.gate == AllocateQubitGate() because isinstance works when the class inherits from it?
            or isinstance(cmd.gate, AllocateDirtyQubitGate)): # Question: is this in use?
            return True
        return False

    def receive(self, command_list):
        """
        Receive list of commands from previous compiler engines.
        Commands are sent on unless they interact with a dirty qubit
        """
        for cmd in command_list:
            # Question: how to handle flush-gates?
            # Question: and FastForwardingGates?
            print("\nInspecting command:")
            print(cmd)
            # Naive flush
            if cmd.gate == FlushGate:
                print("Received FlushGate")
                for cmds in self._cached_cmds:
                    for cmd in cmds:
                        self.send([cmd])
            elif self._is_involved(cmd) or self._is_dirty_alloc(cmd):
                print("Caching")
                self._cache_cmd(cmd)
                self._check_and_send()
            else:
                print("Sending")
                self.send([cmd])

    def print_state(self):
        """
        Helper for "debugging"
        """
        print("Involved Qubit IDs")
        print(self._involved_qubits)
        print("Cached cmds:")
        for i in range(len(self._cached_cmds)):
            print("Acting on qubit " + str(i))
            for cmd in self._cached_cmds[i]:
                print("   " + str(cmd))
