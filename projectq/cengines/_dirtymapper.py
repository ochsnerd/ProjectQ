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
from projectq.types import BasicQubit
from projectq.meta import DirtyQubitTag, DirtyQubitManagementError
from projectq.ops import (AllocateQubitGate,
                          AllocateDirtyQubitGate,
                          DeallocateQubitGate,
                          FlushGate,
                          FastForwardingGate,
                          NotInvertible)
from projectq.ops._gates import *

"""
-   Maybe have qubit_cache class -> better encapsulation + invariance-handling
        Because now, for example in send_qubit_pipeline : aboslute clusterfuck
        to make sure the "last" load doesn't get deleted
-   Extend default_costs
-   test shor: a=2 (not random), n=15 -> 0, 1/4, 1/2, 3/4 with equal prob

-   "Implement" Shor:
    -   change ln 115 to own decomposition rule
    -   write own add_constant in /projectq/libs/math/_constantmath.py
            (from paper)
    -   write own add_constant_modN (also from paper)
    -   use it in other funtions
    -   write own _default_rules.py

-   remove verbose
"""


class DirtyQubitMapper(BasicEngine):
    """
    DirtyQubitMapper is a compiler engine that attempts to remap dirty qubits
    into other qubits to reduce the width of the circuit.

    A dirty qubit is a qubit that:\n
    - Can be allocated in a general state\n
    - Is in exactly the same state when it gets allocated and deallocated

    Both of these conditions can not be feasibly checked by the engine, so
    the user is responsible to ensure they are met. If a qubit meets the
    conditions, it can be flagged as dirty when it is allocated.

    Example:
            .. code-block:: python

                    eng.allocate_qubit(dirty=True)

    The DirtyQubitMapper then caches all gates acting on that qubit as well
    as all gates acting on qubits influenced by that qubit (for example gates
    on a qubit after a CNOT controlled on the dirty qubit acted on it).
    The size of this cache can be controlled by the cache_limit-Argument. A
    bigger cache can lead to longer compilation times, while a smaller one can
    prevent DirtyQubitMapper from remapping.

    There is also the possibility to specify whether FastForwardingGates are
    cached or sent on. Not caching them means that some dirty qubits will
    not be remapped, while caching them can lead to suboptimal results of
    other compiler engines.

    After the deallocation gate on the dirty qubit is received, the
    DirtyQubitMapper will search for qubits that are not influenced by the
    dirty qubit and remap all gates acting on the dirty qubit into that qubit
    (due to the two conditions on dirty qubits this results in an equivalent
    circuit requiring one less qubit).

    The DirtyQubitMapper also estimates the load on each qubit in order to be
    able to choose a good target for remapping (avoid "serialization" by always
    mapping into to the same qubit).

    To this end, a dict of {GateClass: Cost} can be given,
    which indicates the cost of perforimg each gate (this is dependent on
    which infrastructure the circuit is performed).
    """
    def __init__(self,
                 verbose=False,
                 ignore_FastForwarding=False,
                 cache_limit=200,
                 gate_costs=None):
        """
        Initialize a DirtyQubitMapper object.

        Args:
            ignore_FastForwarding (bool): Controls if FastForwadingGates are
                cached, False means they are not cached
            cache_limit (int): controls how many gates per qubit are cached
            gate_costs (dict): Indicates the cost of an operation.\n
                Key: BasicGate object, Value: Int\n
                If a gate is not found in the dict, the cost of it's inverse
                will be used. If that isn't available, the cost of
                gate_costs[BasicGate] is used. If this is also not available, a
                cost of 1 will be assumed.
        """
        BasicEngine.__init__(self)

        # information is cached in the following data-structure:
        # a dict with qubit-ids as keys, and a list containing a list, an int
        # and another list. the first list holds all cached commands acting on
        # the qubit, the int holds the load on the qubit before the cached
        # commands act on it and the second list stores the qubit load after
        # the command cached the corresponding position in the first list has
        # been carried out (the lower these loads, the less work is done on the
        # qubit. This means qubits with low loads are circuit becomes if
        # operations are mapped into the qubit)
        # Qubits for which the deallocate-command has been sent on are deleted
        # from the dict
        # Example:
        # _cache == {1: [[], [10]], 3:[[], [2]]}
        # no commands are cached on qubit 1 and 3. Qubit 2 has not yet been
        # allocated or was already deallocated. Mapping into qubit 3 would be
        # best.
        self._cache = dict()

        self._ignore_FF = ignore_FastForwarding
        self._verbose = verbose
        self._cache_limit = cache_limit
        self._manualmap = -1

        if gate_costs is None:
            self._default_cost = 1
            self._gate_costs = self._default_costs()
        else:
            try:
                self._default_cost = gate_costs[BasicGate]
            except KeyError:
                self._default_cost = 1
            self._gate_costs = gate_costs

    def is_meta_tag_handler(self, tag):
        if tag == DirtyQubitTag:
            return True
        else:
            return False

    def is_available(self, cmd):
        return True

    def _default_costs(self):
        """
        Returns a dict containing default costs for common gates
        """
        costs = dict()

        costs[HGate] = self._default_cost
        costs[XGate] = self._default_cost

        return costs

    def _get_gate_cost(self, gate):
        """
        Get the cost of a gate. First looks for an entry to the gate in the
        _gate_costs-dict. Then it looks for the inverse of that gate. If
        nothing is found aswell, the default cost is returned.
        Args:
            gate (Gate object): get by cmd.gate
        Returns:
            (int) The cost of gate
        """
        try:
            gate_cost = self._gate_costs[type(gate)]
        except KeyError:
            try:
                gate_cost = self._gate_costs[type(gate.get_inverse())]
            except (KeyError, NotInvertible):
                gate_cost = self._default_cost
        return gate_cost

    def _add_loads(self, cmd):
        """
        Determines the load on all involved qubits after cmd has been executed
        and updates their cost-history
        Args:
            cmd (Command object): Command that contains the gate and qubits
        """
        if isinstance(cmd.gate, FlushGate):
            return

        gate_cost = self._get_gate_cost(cmd.gate)

        if isinstance(cmd.gate, AllocateQubitGate):
            assert not self._cache[cmd.qubits[0][0].id][1], (
                "Load on qubit before allocation")
            self._cache[cmd.qubits[0][0].id][1].append(gate_cost)
            return

        involved_IDs = self._get_cmd_IDs(cmd)

        max_load = 0
        for ID in involved_IDs:
            if self._cache[ID][1][-1] > max_load:
                max_load = self._cache[ID][1][-1]

        self._print("cmd_cost = " + str(gate_cost))

        max_load += gate_cost
        for ID in involved_IDs:
            if self._cache[ID][0]:
                # we are caching commands and thus want a cost-history
                self._cache[ID][1].append(max_load)
            else:
                self._cache[ID][1][0] = max_load

    def _send_qubit_pipeline(self, snd_ID, n):
        """
        Sends out the first n cached commands acting in the snd_ID-qubit
        """
        try:
            cmds = self._cache[snd_ID][0]  # for readability
        except KeyError:
            self._print("Trying to send out invalid qubit cache")
            return

        self._print("Sending on " + str(n) +
                    " commands on qubit " + str(snd_ID))
        sent_deallocate = False  # to be able to later invalidate snd_ID

        for i in range(min(n, len(cmds))):
            other_IDs = self._get_cmd_IDs(cmds[i], snd_ID)

            for ID in other_IDs:
                try:
                    # find position of the cmd in the list of the other qubit
                    cmd_pos = 0
                    while self._cache[ID][0][cmd_pos] != cmds[i]:
                        cmd_pos += 1

                    # send all commands acting on the other qubit which were
                    # cached before this cmd
                    self._send_qubit_pipeline(ID, cmd_pos)

                    # all previous commands on the other qubit were sent on,
                    # we delete the one we're inspecting now in the other list
                    # to avoid sending it multiple times
                    self._cache[ID][0] = self._cache[ID][0][1:]
                    if len(self._cache[ID][1]) > 1:
                        self._cache[ID][1] = self._cache[ID][1][1:]
                except KeyError:
                    print("Invalid qubit pipeline encountered (in the" +
                          " process of shutting down?).")
            # all commands interacting with our current one have been sent on
            # we can send the current one
            self.send([cmds[i]])
            if isinstance(cmds[i].gate, DeallocateQubitGate):
                sent_deallocate = True

        # remove the cmds we just sent on from _cache
        del self._cache[snd_ID][0][:n]
        # delete associated cost-entries, but keep the last one
        if len(self._cache[snd_ID][1]) <= n:
            del self._cache[snd_ID][1][:-1]
        else:
            del self._cache[snd_ID][1][:n]

        if sent_deallocate:
            # invalidate ID if we sent the deallocation
            assert not self._cache[snd_ID][0], (
                   "Attempting to delete non-empty cache")
            del self._cache[snd_ID]

    def _get_cmd_pos(self, ID, i):
        """
        Return a list of tuples containing two ints:
        The ID of  all* qubits involved in the i-th command in qubit ID and
        the position of that command in it's respective _cache-list.
        *EXCLUDES THE ID ITSELF
        Args:
            ID (int): qubit index
            i (int): command position in qubit ID's command list
        Returns:
            (list of tuples) [(ID, pos),(ID, pos)]
        """
        cmd = self._cache[ID][0][i]
        other_IDs = self._get_cmd_IDs(cmd, ID)

        # 1-qubit gate: only gate at index i is involved
        if not other_IDs:
            return []

        # When the same gate appears multiple times, we need to make sure
        # not to match earlier instances of the gate applied to the same
        # qubits. So we count how many there are, and skip over them when
        # looking in the other lists.
        n_identical_to_skip = sum(1
                                  for prv_cmd in self._cache[ID][0][:i]
                                  if prv_cmd == cmd)
        id_pos_pairs = []
        for other_ID in other_IDs:
            ident_idx = [i
                         for i, c in enumerate(self._cache[other_ID][0])
                         if c == cmd]
            id_pos_pairs.append((other_ID, ident_idx[n_identical_to_skip]))
        return id_pos_pairs

    def _find_target(self, rmp_ID):
        """
        Finds a valid (i.e. not interacting with the dirty qubit) qubit
        (target) to map the qubit with rmp_ID into.
        Prefers qubits indicated by the DirtyQubitTag.
        Also prefers (weaker) qubits with less load.
        If _manualmap was set, uses that as target WITHOUT CHECKING INVOLVEMENT
        and resets _manualmap after.
        If no manualmap was set and no uninvolved qubit is found, returns None

        Args:
            rmp_ID (int): ID of the qubit to remap
        Returns:
            (int) or None; ID of the most suitable remap target
        Raises:
            DirtyQubitManagementError:
                If the provided target is not active, i.e. it hasn't been
                allocated or was already deallocated
        """
        if self._manualmap != -1:
            t = self._manualmap
            if t not in self._cache:
                raise DirtyQubitManagementError(
                    "The manually provided target qubit has not " +
                    "yet been allocated or was already deallocated")
            self._manualmap = -1
            return t

        def check_involvement(check_ID, j, ID_set):
            """
            Deletes IDs out of the ID_set if they are involved with a command
            after the j-th command on qubit check_ID

            Args:
                check_ID (int): ID of the qubit to check involvement with
                j (int): Position in the cache of check_ID after which
                    involvement is of interest
                ID_set (set): set of qubit IDs (int) to remove involved qubits
                    from
            Returns:
                (set(int)) ID_set, but IDs of all involved qubits are removed
            """
            for i, cmd in enumerate(self._cache[check_ID][0]):
                if i <= j:
                    # jumping over already inspected commands
                    continue
                other_ID_pos = self._get_cmd_pos(check_ID, i)
                for ID, pos in other_ID_pos:
                    if ID in ID_set:
                        ID_set.remove(ID)
                    ID_set = check_involvement(ID, pos, ID_set)
            return ID_set

        def get_lowest_load_id(possible_ids):
            """
            Returns the lowest load on qubits indicated in possible IDs
            Args:
                possible_ids (set(int)): Set of IDs of qubits to find the one
                    with the lowest load from
            Returns:
                (int) ID of the qubit with the lowest load
            """
            lowest_load = None
            corresponding_id = -1
            for ID in possible_ids:
                try:
                    load = self._cache[ID][1][-1]
                except KeyError:
                    print(possible_ids)
                    self.print_state(override=True)
                    raise KeyError()
                if lowest_load is None:
                    lowest_load = load
                    corresponding_id = ID
                elif lowest_load > load:
                    lowest_load = load
                    corresponding_id = ID
            return corresponding_id

        preferred_qubits = set()
        # check if we have preferred targets for this dirty qubit
        for tag in self._cache[rmp_ID][0][0].tags:
            if isinstance(tag, DirtyQubitTag):
                # maybe there are IDs in target_IDs that are not active
                preferred_qubits = {ID for ID in tag.target_IDs
                                    if ID in self._cache}

        if preferred_qubits:
            # we found preferred targets
            preferred_qubits = check_involvement(rmp_ID,
                                                 0,
                                                 preferred_qubits)
            if preferred_qubits:
                # some of the preferred targets can be mapped into
                self._print("Found preferred, possible qubits: "
                            + str(preferred_qubits))
                self._print("Remapping into " + str(list(preferred_qubits)[0]))
                return get_lowest_load_id(preferred_qubits)

        possible_qubits = {ID for ID in self._cache}
        possible_qubits.remove(rmp_ID)

        possible_qubits = check_involvement(rmp_ID,
                                            0,
                                            possible_qubits)

        self._print("Found qubits that we can map into: " +
                    str(possible_qubits))

        if not possible_qubits:
            return None

        self._print("Remapping into " + str(list(possible_qubits)[0]))

        return get_lowest_load_id(possible_qubits)

    def _remap_dqubit(self, rmp_ID):
        """
        Remaps the operations on deallocated dirty qubit to a qubit not
        interacting with that particular qubit (target), if such a qubit
        exists and the whole lifecycle of the dirty qubit is cached.
        Returns the ID of the target, plus whether the target was
        involved beforehand, ie whether the commands have to stay cached or
        can be sent on. Also updates the loads that get changed after remapping
        Args:
            rmp_ID (int): ID of dirty qubit to remap
        Returns:
            (int, bool) Returns the ID of the qubit that the dirty qubit was
                mapped into (target) as well as a flag indicating whether
                commands on the target can be sent on
        """
        self._print("Remapping deallocated dqubit")

        assert self._is_dirty_alloc(self._cache[rmp_ID][0][0])
        assert self._is_dirty_dealloc(self._cache[rmp_ID][0][-1])

        # No gates performed on dqubit other than allocate/deallocate
        # we just have to clean up
        if self._cache[rmp_ID][0][1:-1] == []:
            self._print("Don't have to remap 'empty' dqubit")
            del self._cache[rmp_ID]
            return rmp_ID, True  # don't have to send anything

        new_ID = self._find_target(rmp_ID)

        # maybe there is no possible qubit to remap to
        if new_ID is None:
            return rmp_ID, False

        # remove allocate and deallocate command
        self._cache[rmp_ID][0] = self._cache[rmp_ID][0][1:-1]

        # Change ID of qubits of cached commands
        for cmd in self._cache[rmp_ID][0]:
            for sublist in cmd.all_qubits:
                for qubit in sublist:
                    if qubit.id == rmp_ID:
                        qubit.id = new_ID

        wait = None  # set later, controls whether cached cmds are sent on
        upd_start = None  # set later, determines where the load-update start

        # append commands acting on qubit rmp_ID to list of qubit new_ID
        if not self._cache[new_ID][0]:
            # Mapping into a not involved qubit
            self._print("Map to not involved")
            upd_start = -1
            self._cache[new_ID][0] = self._cache[rmp_ID][0]
            wait = False
        else:
            # Mapping into a involved qubit
            self._print("Map to involved")
            upd_start = len(self._cache[new_ID][0])
            if isinstance(self._cache[new_ID][0][-1].gate,
                          DeallocateQubitGate):
                # The qubit we map to is already deallocated, but still cached
                # sneak in the cmds of the dqubit before the deallocation
                upd_start -= 1
                self._cache[new_ID][0] = (self._cache[new_ID][0][:-1]
                                          + self._cache[rmp_ID][0]
                                          + [self._cache[new_ID][0][-1]])
            else:
                self._cache[new_ID][0].extend(self._cache[rmp_ID][0])
            wait = True

        def update_load(upd_ID, start_pos):
            """
            Update the load on upd_ID after start_pos
            """
            self._print("Updating qubit " + str(upd_ID))
            curr_pos = start_pos
            for cmd in self._cache[upd_ID][0][start_pos+1:]:
                # The true cost is either the load of this qubit before
                # the current cmd plus the cost of the command OR the load on
                # the other involved qubits (if there are any) after the
                # command, whichever is bigger
                curr_pos += 1
                self._print("  Looking at " + str(cmd) + " at pos "
                            + str(curr_pos))
                load_this = (self._cache[upd_ID][1][curr_pos-1] +
                             self._get_gate_cost(cmd.gate))
                self._print("  load_this = " + str(load_this))

                other_ID_pos = self._get_cmd_pos(upd_ID, curr_pos)
                if other_ID_pos:  # There are other qubits involved in cmd
                    self._print("  Other qubits involved")
                    # The load on the other qubits should be all the same,
                    # but since we put in a placeholder for the target,
                    # we have to make sure to get the correct value
                    load_others = max([self._cache[ID][1][pos]
                                      for ID, pos in other_ID_pos])
                    self._print("  load_others = " + str(load_others))
                    if load_this > load_others:
                        # the qubit we mapped into has a bigger load
                        # than the others -> we have to update the others
                        for ID, pos in other_ID_pos:
                            self._cache[ID][1][pos] = load_this
                            update_load(ID, pos)
                    else:
                        # The new qubit has a smaller load than the others
                        # -> we only have to update the load on the new qubit
                        load_this = load_others

                self._cache[upd_ID][1][curr_pos] = load_this
                self._print("  Setting " + str(cmd) + " to " + str(load_this))

        # Add "empty" entries into load-history to be updated
        self._cache[new_ID][1] += [0] * len(self._cache[rmp_ID][0])
        if upd_start == -1:
            # If we map into a not involved qubit, we have one too many cost
            # entries, but want to keep the cost of the uncached gates
            self._cache[new_ID][1].pop()
            # Updating first cost-entry "manually" (not in function),
            # this could be avoided if we saved the load on each qubit from
            # uncached cmds seperately
            load_new_qb = (self._cache[new_ID][1][0] +
                           self._get_gate_cost(self._cache[new_ID][0][0].gate))
            # take 2nd cost entry from rmp_ID because we deleted the alloc-cmd
            load_old_qb = self._cache[rmp_ID][1][1] - self._cache[rmp_ID][1][0]
            if load_new_qb > load_old_qb:
                # the qubit we mapped into has a bigger load than the others
                # -> we have to update the others
                self._print("First update: Updating others")
                other_ID_pos = self._get_cmd_pos(new_ID, 0)
                for ID, pos in other_ID_pos:
                    self._cache[ID][1][pos] = load_new_qb
                    update_load(ID, pos)
                self._cache[new_ID][1][0] = load_new_qb
            else:
                # The new qubit has a smaller load than the others -> we only
                # have to update the load on the new qubit
                self._print("First update: Updating target")
                self._cache[new_ID][1][0] = load_old_qb
            upd_start += 1

        update_load(new_ID, upd_start)

        del self._cache[rmp_ID]

        self._print("After remapping dqubit")
        self.print_state()

        # return new_ID, wait
        return new_ID, True

    def _check_and_send(self):
        """
        Checks the state of _cache:
        -   If there is a dirty qubit that we can remap (both dirty allocate
            and deallocate cached), then it does that. If there is no other
            involvement, the commands are sent on to the next engine
        -   If we don't ignore FastForwardingGates, these are sent on
        -   If we exceed the cache-limit, commands are sent on
        """
        self._print("Called checkandsend")
        # Note on structure:
        # We loop over a dict while possibly removing keys from it.
        # To make sure we handle every entry, each time a key is deleted,
        # the loop is restarted. This is achieved by:
        #   while True
        #       for key in mydict:
        #           if condition(key):
        #               del mydict[key]
        #               break
        #       else:
        #           break
        # Each time the if-block is executed, we break out of the for-loop
        # to the end of the while statement (skipping the else). When the
        # condition evaluates to False every iteration of one for-loop, the
        # else-statement is evaluated and we break out of the while-loop.
        while True:
            for ID, [cmd_list, cost_list] in self._cache.items():
                assert (len(cmd_list) == len(cost_list) or
                        not cmd_list and len(cost_list) == 1), (
                        "Cost-history and command-cache don't match")

                if not cmd_list:
                    # no cached cmds - we don't have to check anything
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
                        n = len(self._cache[mappedinto_ID][0])
                        self._send_qubit_pipeline(mappedinto_ID, n)

                    # by remapping the dirty qubit, we changed the size of the
                    # dict we were looping though - we stop and restart
                    break
                if not cmd_list:
                    # dirty qubit was remapped - no more cached cmds acting
                    # on this qubit
                    continue

                if (isinstance(cmd_list[-1].gate, FastForwardingGate) and
                        not self._ignore_FF):
                    self._send_qubit_pipeline(ID, len(cmd_list))
                    break

                if len(cmd_list) > self._cache_limit:
                    self._send_qubit_pipeline(ID, len(cmd_list))
                    break
            else:
                break

    def _get_cmd_IDs(self, cmd, exclude=None):
        """
        Returns a list of the IDs of all qubits involved in cmd,
        excluding the qubit with ID exclude
        """
        if exclude is None:
            return [qb.id
                    for qureg in cmd.all_qubits
                    for qb in qureg]
        else:
            return [qb.id
                    for qureg in cmd.all_qubits
                    for qb in qureg
                    if qb.id != exclude]

    def _cache_cmd(self, cmd):
        """
        Caches a command (adds a copy of the command to each list the qubits it
        acts on)
        """
        ID_list = self._get_cmd_IDs(cmd)
        for ID in ID_list:
            self._cache[ID][0].append(cmd)

    def _is_involved(self, cmd):
        """
        Checks if cmd acts on an involved qubit
        """
        ID_list = self._get_cmd_IDs(cmd)
        for ID in ID_list:
            if self._cache[ID][0]:
                return True
        return False

    def _is_dirty_alloc(self, cmd):
        """
        Checks if cmd is allocation of a dirty qubit
        """
        if (any(isinstance(tag, DirtyQubitTag) for tag in cmd.tags)
                and isinstance(cmd.gate, AllocateQubitGate)):
            return True
        return False

    def _is_dirty_dealloc(self, cmd):
        """
        Checks if cmd is deallocation of a dirty qubit
        """
        if (any(isinstance(tag, DirtyQubitTag) for tag in cmd.tags)
                and isinstance(cmd.gate, DeallocateQubitGate)):
            return True
        return False

    def receive(self, command_list):
        """
        Receive list of commands from previous compiler engines.
        Commands are sent on unless they interact with a dirty qubit

        Args:
            command_list (list<Command>): List of commands to receive

        Raises:
            DirtyQubitManagementError:
                If a qubit deallocation gate on a qubit is received before it's
                allocation gate.ls
        """
        for cmd in command_list:
            self._print("Inspecting command:")
            self._print(str(cmd) + ", tags: " + str(cmd.tags))

            # updating _cache
            if isinstance(cmd.gate, AllocateQubitGate):
                new_ID = cmd.qubits[0][0].id
                self._print("Adding qubit " + str(new_ID) + " to cached_cmds")
                self._print(len(self._cache))
                self._cache[new_ID] = [[], []]

            self._add_loads(cmd)

            if isinstance(cmd.gate, FlushGate):
                # received flush-gate - send on all cached cmds
                self._print("Received FlushGate")
                for ID in list(self._cache):
                    cmds_to_send = len(self._cache[ID][0])
                    self._send_qubit_pipeline(ID, cmds_to_send)
            elif self._is_involved(cmd) or self._is_dirty_alloc(cmd):
                # received command acting on a qubit already involved or an
                # allocation of a dirty qubit -> cache the command
                self._print("Caching")
                self._cache_cmd(cmd)
                self._check_and_send()
            else:
                # the received command doesn't concern us, we update our cache
                # and then send it on
                self._print("Forwarding")
                if isinstance(cmd.gate, DeallocateQubitGate):
                    self._print("Invalidating qubit ID")
                    dealloc_ID = cmd.qubits[0][0].id
                    try:
                        [cmds, _] = self._cache.pop(dealloc_ID)
                    except KeyError:
                        raise DirtyQubitManagementError(
                            "A qubit which was not allocated was deallocated")
                self.send([cmd])
        self.print_state()
        self._print("\n\n")

    def set_next_target(self, qubit):
        """
        Manually set the next target. The next dirty qubit that gets
        deallocated will be mapped into the provided qubit.

        Warning:
            No involvement-check is performed. If the dirty qubit
            interacts with the provided target, the remapping will change the
            behaviour of the circuit!
        Args:
            qubit (Qubit object): target qubit
        """
        assert(not isinstance(qubit, tuple))
        if isinstance(qubit, BasicQubit):
            self._manualmap = qubit.id
        else:
            self._manualmap = qubit[0].id

    """
    Helpers for "debugging"
    """
    def _print(self, message):
        if self._verbose:
            print(message)

    def print_state(self, override=False):
        if not self._verbose and not override:
            return
        print("----------------------------------")
        print("State of dirtymapper:")
        for qubit_id, [cmds, costs] in self._cache.items():
            print("  Qubit " + str(qubit_id))
            print("    Cached Commands:")
            if not cmds:
                print("      [], " + str(costs))
            else:
                for cmd, cost in zip(cmds, costs):
                    print("      " + str(cmd) + ", " + str(cost))
        print("----------------------------------")
