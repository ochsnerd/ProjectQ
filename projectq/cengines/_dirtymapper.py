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
        
        # Maybe better: dict with ID:cached_cmds_index that tracks all qubits 
        # and has a cached_cmds_index of None if the qubit is not involved
        self._all_qubits = []
        
        self.manualmap = -1

    def is_meta_tag_handler(self, tag):
        if tag == DirtyQubitTag:
            return True
        else:
            return False

    def is_available(self, cmd):
        return True
        
    def _send_qubit_pipeline(self, snd_ID, n):
        """
        Sends out n cached commands acting in the snd_ID-qubit
        """
        
        print("Sending on " + str(n) + " commands on qubit " + str(snd_ID))
        snd_index = self._find_index(snd_ID)
        snd_cmds = self._cached_cmds[snd_index] # for readability
        
        for i in range(min(n,len(snd_cmds))):
            other_involved_qubits = [qb
                                     for qreg in snd_cmds[i].all_qubits
                                     for qb in qreg
                                     if qb.id != snd_ID]

            
            for qb in other_involved_qubits:
                index = self._find_index(qb.id)
                try:
                    # find position of the cmd in the list of the other qubit
                    cmd_pos = 0
                    while self._cached_cmds[index][cmd_pos] != snd_cmds[i]:
                        cmd_pos += 1
                        
                    # send all commands acting on the other qubit which were 
                    # cached before this cmd
                    self._send_qubit_pipeline(qb.id, cmd_pos)
                    
                    # all previous commands on the other qubit were sent on,
                    # we delete the one we're inspecting now in the other list
                    # to avoid sending it multiple times
                    self._cached_cmds[index] = self._cached_cmds[index][1:]
                except IndexError:
                    print("Invalid qubit pipeline encountered (in the"
                          " process of shutting down?).")
            # if we're sending a deallocate-gate, we have to update _all_qubits
            if isinstance(snd_cmds[i].gate, DeallocateQubitGate):
                assert(snd_cmds[i].qubits[0][0].id in self._all_qubits)
                self._all_qubits.remove(snd_cmds[i].qubits[0][0].id)
            # all commands interacting with our current one have been sent on
            # we can send the current one
            self.send([snd_cmds[i]])
        
        if len(snd_cmds) <= n:
            # we sent on all cmds acting on qubit snd_ID
            # delete it from our cache
            del self._cached_cmds[snd_index]
            del self._involved_qubits[snd_index]
        else:
            # we still have cached cmds acting on qubit snd_ID
            # only remove the cmds we have sent
            self._cached_cmds[snd_index] = self._cached_cmds[snd_index][n:]
        
    def _find_index(self, ID):
        """
        Finds the list-index belonging to the qubit with ID
        """
        for i,_id in enumerate(self._involved_qubits):
            if _id == ID:
                return i
        raise IndexError
        
    def _find_remap_qubitID(self, rmp_ID):
        """
        Finds a valid (i.e. not interacting with the dirty qubit) qubit to map
        the qubit with rmp_ID into. If no such qubit is found, returns None
            -find non-interacting qubit in _involved_qubits (not very likely)
            -obtain ID of allocated, not involved qubit (easy if
                ID < biggest_involved_id, difficult otherwise?)
            -prefer qubits indicated by with DirtyQubits
        """
        if self.manualmap != -1:
            return self.manualmap
        
        possible_qubits = [ID for ID in self._all_qubits]
        possible_qubits.remove(rmp_ID)
        
        for cmd in self._cached_cmds[self._find_index(rmp_ID)]:
            other_involved_qubits = [qb
                                     for qreg in cmd.all_qubits
                                     for qb in qreg
                                     if qb.id != rmp_ID]
            for qb in other_involved_qubits:
                if qb.id in possible_qubits:
                    possible_qubits.remove(qb.id)
        
        print("Found qubits that we can map into:")
        print(possible_qubits)
        
        if possible_qubits == []:
            return None
    
        return possible_qubits[0]
        
    def _remap_dqubit(self, rmp_ID):
        """
        Remaps the operations on deallocated dirty qubit to a qubit not
        interacting with that particular qubit, if such a qubit exists.
        Returns the ID of the qubit it mapped the dirty qubit into
        """
        print("Remapping deallocated dqubit")
        assert(rmp_ID in self._involved_qubits)
        
        # find index of dqubit in our list
        rmp_index = self._find_index(rmp_ID)
                    
        assert(self._is_dirty_alloc(self._cached_cmds[rmp_index][0]))
        
        # No gates performed on dqubit other than allocate/deallocate
        # we just have to clean up
        if self._cached_cmds[rmp_index][1:-1] == []:
            print("Don't have to remap 'empty' dqubit")
            self._cached_cmds[rmp_index] = self._cached_cmds[rmp_index][1:-1]
            self._all_qubits.remove(rmp_ID)
            return rmp_ID
        
        new_ID = self._find_remap_qubitID(rmp_ID)
        print("Mapping into " + str(new_ID))
        # maybe there is no possible qubit to remap to
        if new_ID == None:
            return rmp_ID
    
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
            print("Map to not involved")
            self._involved_qubits[rmp_index] = new_ID
        else:
            print("Map to involved")
            new_index = self._find_index(new_ID)
            # DOES THIS REALLY WORK / IS THIS NEEDED? SEE test3
            if isinstance(self._cached_cmds[new_index][-1], DeallocateQubitGate):
                # The gate we map to is already deallocated
                # sneak in the cmds of the dqubit before the deallocation
                self._cached_cmds[new_index] =(self._cached_cmds[new_index][:-1]
                                             + self._cached_cmds[rmp_index]
                                             + self._cached_cmds[new_index][-1])
            else:
                self._cached_cmds[new_index].extend(self._cached_cmds[rmp_index])
            del self._involved_qubits[rmp_index]
            del self._cached_cmds[rmp_index]
            
        # update _all_qubits
        self._all_qubits.remove(rmp_ID)
        
        print("After remapping dqubit")
        self.print_state()
        
        return new_ID

    def _check_and_send(self):
        """
        Checks if the last cmds in _cached_cmds are DeallocateQubitGate 
        acting on dirty qubits. If so, they are remapped if
        possible and then sent to the next engine
        """
        print("Called checkandsend")
        for i,ID in enumerate(self._involved_qubits):
            # Question: Is just looking at the last gate correct? This would not
            # work if you apply an ordinary gate right after measuring and some-
            # how send them in a list?
            last_cmd = self._cached_cmds[i][-1] # for readability
            assert(ID in [qubit.id
                          for sublist in last_cmd.all_qubits
                          for qubit in sublist])
            if (self._is_dirty_alloc(self._cached_cmds[i][0]) and
                isinstance(last_cmd.gate, DeallocateQubitGate)):
                print("Dirty deallocate detected")
                ninvolved_before = len(self._involved_qubits)
                mappedinto_ID = self._remap_dqubit(ID)
                
                # if we mapped to an involved qubit, we wait,
                # else we send on the cmds
                if len(self._involved_qubits) == ninvolved_before:
                    n = len(self._cached_cmds[self._find_index(mappedinto_ID)])
                    self._send_qubit_pipeline(mappedinto_ID,n)
                # check if _involved_qubits is still correct
                for i in range(len(self._involved_qubits)):
                    if self._cached_cmds[i] == []:
                        del self._involved_qubits[i]
                        del self._cached_cmds[i]

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
            print("Inspecting command:")
            print(cmd)
            if isinstance(cmd.gate, AllocateQubitGate):
                print("Adding ID to _all_qubits")
                self._all_qubits.append(cmd.qubits[0][0].id)
            # Naive flush
            if isinstance(cmd.gate,FlushGate):
                print("Received FlushGate")
                for i,ID in enumerate(self._involved_qubits):
                    self._send_qubit_pipeline(ID, len(self._cached_cmds[i]))
                self._involved_qubits = []
            elif self._is_involved(cmd) or self._is_dirty_alloc(cmd):
                print("Caching")
                self._cache_cmd(cmd)
                self._check_and_send()
            else:
                print("Forwarding")
                if isinstance(cmd.gate, DeallocateQubitGate):
                    print("Deleting from _all_qubits")
                    assert(cmd.qubits[0][0].id in self._all_qubits)
                    self._all_qubits.remove(cmd.qubits[0][0].id)
                self.send([cmd])
        self.print_state()
        print("\n\n")

    def print_state(self):
        """
        Helper for "debugging"
        """
        print("State of dirtymapper:")
        print("All active qubits")
        print(self._all_qubits)
        print("Involved Qubit IDs")
        print(self._involved_qubits)
        print("Cached cmds:")
        for i, ID in enumerate(self._involved_qubits):
            print("Acting on qubit " + str(ID))
            for cmd in self._cached_cmds[i]:
                print("   " + str(cmd))
