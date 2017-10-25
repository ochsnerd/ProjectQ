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

class DirtyQubitReplacer(BasicEngine):
    def __init__(self):
        BasicEngine.__init__(self)
        self.received_commands = []
        watcher_queue = [Sender(self)]  # need pointer to itself -- google it

    def is_available(self, cmd):
        return True

    def receive(self, command_list):
        self.received_commands.extend(command_list)
        print("Hello World")
        if not self.is_last_engine:
            self.send(command_list)
        else:
            pass
    
    #~ def receive(self, command_list):
        #~ look through command list:
            #~ if dirty_qubit_allocated:
                #~ allocate watcher
                #~ append watcher to watcher_queue (next_watcher = watcher_queue[0])
                #~ set now second_to_last watcher in queue to newly added one
        #~ send commands through watcher_queue
        
        
    #~ class Watcher():
        #~ def __init__(self, next_watcher, watched_dqubit):
            #~ next_watcher = next_watcher
            #~ watched_dqubit = watched_dqubit
            #~ watchlist = []
            #~ buffer = []
            
        #~ def receive(self, command_list):
            #~ look through command list:
                #~ if watched_dqubit deallocated:
                    #~ switch tags on commands acting on watched_dqubit to ANOTHER QUBIT -- WHICH?
                #~ if interaction between qbit on watchlist and qubit not on watchlist:
                    #~ add qubit to watchlist
                #~ if command acting on qbuit in watchlist:
                    #~ add command to buffer
                #~ else:
                    #~ next_watcher.receive(command)
                    
    #~ class Sender():
        #~ def __init__(self, mom)
            #~ mom = mom
            
        #~ def receive(self, command_list)
            #~ mom.send(command_list)
        
            
