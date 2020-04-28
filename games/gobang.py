import datetime
import math
import os

import gym
import numpy
import torch

from .abstract_game import AbstractGame


class MuZeroConfig:
    def __init__(self):
        self.seed = 0  # Seed for numpy, torch and the game

        self.board_size = 8

        ### Game
        self.observation_shape = (4, self.board_size, self.board_size)  # Dimensions of the game observation, must be 3 (channel, height, width). For a 1D array, please reshape it to (1, 1, length of array)
        self.action_space = [i for i in range(self.board_size * self.board_size)]  # Fixed list of all possible actions. You should only edit the length
        self.players = [i for i in range(2)]  # List of players. You should only edit the length
        self.stacked_observations = 0  # Number of previous observations and previous actions to add to the current observation

        ### Evaluate
        self.muzero_player = 0  # Turn Muzero begins to play (0: MuZero plays first, 1: MuZero plays second)
        self.opponent = "random"  # Hard coded agent that MuZero faces to assess his progress in multiplayer games. It doesn't influence training. None / "random" / "expert" if implemented in the Game class



        ### Self-Play
        self.num_actors = 3  # Number of simultaneous threads self-playing to feed the replay buffer
        self.max_moves = self.board_size * self.board_size * 2  # Maximum number of moves if game is not finished before
        self.num_simulations = 100  # Number of future moves self-simulated
        self.discount = 1  # Chronological discount of the reward
        self.temperature_threshold = 30  # Number of moves before dropping temperature to 0 (ie playing according to the max)

        # Root prior exploration noise
        self.root_dirichlet_alpha = 0.03
        self.root_exploration_fraction = 0.25

        # UCB formula
        self.pb_c_base = 19652
        self.pb_c_init = 1.25



        ### Network
        self.network = "resnet"  # "resnet" / "fullyconnected"
        self.support_size = 10  # Value and reward are scaled (with almost sqrt) and encoded on a vector with a range of -support_size to support_size
        
        # Residual Network
        self.downsample = False  # Downsample observations before representation network (See paper appendix Network Architecture)
        self.blocks = 2  # Number of blocks in the ResNet
        self.channels = 8  # Number of channels in the ResNet
        self.reduced_channels = 8  # Number of channels before heads of dynamic and prediction networks
        self.resnet_fc_reward_layers = [5]  # Define the hidden layers in the reward head of the dynamic network
        self.resnet_fc_value_layers = [5]  # Define the hidden layers in the value head of the prediction network
        self.resnet_fc_policy_layers = [5]  # Define the hidden layers in the policy head of the prediction network
        
        # Fully Connected Network
        self.encoding_size = 32
        self.fc_reward_layers = [64]  # Define the hidden layers in the reward network
        self.fc_value_layers = []  # Define the hidden layers in the value network
        self.fc_policy_layers = []  # Define the hidden layers in the policy network
        self.fc_representation_layers = []  # Define the hidden layers in the representation network
        self.fc_dynamics_layers = [64]  # Define the hidden layers in the dynamics network



        ### Training
        self.results_path = os.path.join(os.path.dirname(__file__), "../results", os.path.basename(__file__)[:-3], datetime.datetime.now().strftime("%Y-%m-%d--%H-%M-%S"))  # Path to store the model weights and TensorBoard logs
        self.training_steps = 10000  # Total number of training steps (ie weights update according to a batch)
        self.batch_size = 512  # Number of parts of games to train on at each training step
        self.checkpoint_interval = 5  # Number of training steps before using the model for sef-playing
        self.value_loss_weight = 0.25  # Scale the value loss to avoid overfitting of the value function, paper recommends 0.25 (See paper appendix Reanalyze)
        self.training_device = "cuda" if torch.cuda.is_available() else "cpu"  # Train on GPU if available

        self.optimizer = "SGD"  # "Adam" or "SGD". Paper uses SGD
        self.weight_decay = 1e-4  # L2 weights regularization
        self.momentum = 0.9  # Used only if optimizer is SGD

        # Exponential learning rate schedule
        self.lr_init = 0.01  # Initial learning rate
        self.lr_decay_rate = 0.1  # Set it to 1 to use a constant learning rate
        self.lr_decay_steps = 800



        ### Replay Buffer
        self.window_size = 500  # Number of self-play games to keep in the replay buffer
        self.num_unroll_steps = 5  # Number of game moves to keep for every batch element
        self.td_steps = self.max_moves  # Number of steps in the future to take into account for calculating the target value
        self.use_last_model_value = True  # Use the last model to provide a fresher, stable n-step value (See paper appendix Reanalyze)

        # Prioritized Replay (See paper appendix Training)
        self.PER = True  # Select in priority the elements in the replay buffer which are unexpected for the network
        self.use_max_priority = False  # Use the n-step TD error as initial priority. Better for large replay buffer
        self.PER_alpha = 1  # How much prioritization is used, 0 corresponding to the uniform case, paper suggests 1
        self.PER_beta = 1.0



        ### Adjust the self play / training ratio to avoid over/underfitting
        self.self_play_delay = 0  # Number of seconds to wait after each played game
        self.training_delay = 0  # Number of seconds to wait after each training step
        self.ratio = 1  # Desired self played games per training step ratio. Equivalent to a synchronous version, training can take much longer. Set it to None to disable it


    def visit_softmax_temperature_fn(self, trained_steps):
        """
        Parameter to alter the visit count distribution to ensure that the action selection becomes greedier as training progresses.
        The smaller it is, the more likely the best action (ie with the highest visit count) is chosen.
        Returns:
            Positive float.
        """
        if trained_steps < 30:
            return 1.0
        else:
            return 0


class Game(AbstractGame):
    """
    Game wrapper.
    """

    def __init__(self, seed=None):
        self.env = Gobang()

    def step(self, action):
        """
        Apply action to the game.
        
        Args:
            action : action of the action_space to take.

        Returns:
            The new observation, the reward and a boolean if the game has ended.
        """
        observation, reward, done = self.env.step(action)
        return observation, reward, done

    def to_play(self):
        """
        Return the current player.

        Returns:
            The current player, it should be an element of the players list in the config. 
        """
        return self.env.to_play()

    def legal_actions(self):
        """
        Should return the legal actions at each turn, if it is not available, it can return
        the whole action space. At each turn, the game have to be able to handle one of returned actions.
        
        For complex game where calculating legal moves is too long, the idea is to define the legal actions
        equal to the action space but to return a negative reward if the action is illegal.

        Returns:
            An array of integers, subset of the action space.
        """
        return self.env.legal_actions()

    def reset(self):
        """
        Reset the game for a new game.
        
        Returns:
            Initial observation of the game.
        """
        return self.env.reset()

    def close(self):
        """
        Properly close the game.
        """
        pass

    def render(self):
        """
        Display the game observation.
        """
        self.env.render()
        # input("Press enter to take a step ")

    def human_to_action(self):
        """
        For multiplayer games, ask the user for a legal action
        and return the corresponding action number.

        Returns:
            An integer from the action space.
        """
        valid = False
        while not valid:
            valid, action = self.env.human_input_to_action()
        return action
    
    def expert_agent(self):
        """
        Hard coded agent that MuZero faces to assess his progress in multiplayer games.
        It doesn't influence training

        Returns:
            Action as an integer to take in the current game state
        """
        pass

    def action_to_string(self, action):
        """
        Convert an action number to a string representing the action.
        Args:
            action_number: an integer from the action space.
        Returns:
            String representing the action.
        """
        return self.env.action_to_human_input(action)


class Gobang:
    def __init__(self):
        self.board_size = 8
        self.n_in_row = 5
        self.board = numpy.zeros((self.board_size, self.board_size)).astype(int)
        self.player = 1
        self.last_action = -1
        self.board_markers = [
            chr(x) for x in range(ord("A"), ord("A") + self.board_size)
        ]

    def to_play(self):
        return 0 if self.player == 1 else 1

    def reset(self):
        self.board = numpy.zeros((self.board_size, self.board_size)).astype(int)
        self.player = 1
        self.last_action = -1
        return self.get_observation()

    def step(self, action):
        x = math.floor(action / self.board_size)
        y = action % self.board_size
        self.board[x][y] = self.player

        done = self.is_finished()

        reward = 1 if done else 0

        self.last_action = action
        self.player *= -1

        return self.get_observation(), reward, done

    def get_observation(self):
        board_player1 = numpy.where(self.board == 1, 1.0, 0.0)
        board_player2 = numpy.where(self.board == -1, 1.0, 0.0)
        board_last_place = numpy.full((self.board_size, self.board_size), 0).astype(float)
        if self.last_action != -1:
            action = self.last_action
            x = math.floor(action / self.board_size)
            y = action % self.board_size
            board_last_place[x][y] = 1
        board_to_play = numpy.full((self.board_size, self.board_size), self.player).astype(float)
        return numpy.array([board_player1, board_player2, board_last_place, board_to_play])

    def legal_actions(self):
        legal = []
        for i in range(self.board_size):
            for j in range(self.board_size):
                if self.board[i][j] == 0:
                    legal.append(i * self.board_size + j)
        return legal

    def is_finished(self):
        has_legal_actions = False
        directions = ((1, -1), (1, 0), (1, 1), (0, 1))
        for i in range(self.board_size):
            for j in range(self.board_size):
                # if no stone is on the position, don't need to consider this position
                if self.board[i][j] == 0:
                    has_legal_actions = True
                    continue
                # value-value at a coord, i-row, j-col
                player = self.board[i][j]
                # check if there exist self.n_in_row in a line
                for d in directions:
                    x, y = i, j
                    count = 0
                    for _ in range(self.n_in_row):
                        if (x not in range(self.board_size)) or (
                            y not in range(self.board_size)
                        ):
                            break
                        if self.board[x][y] != player:
                            break
                        x += d[0]
                        y += d[1]
                        count += 1
                        # if self.n_in_row in a line, store positions of all stones, return value
                        if count == self.n_in_row:
                            return True
        return not has_legal_actions

    def render(self):
        marker = "  "
        for i in range(self.board_size):
            marker = marker + self.board_markers[i] + " "
        print(marker)
        for row in range(self.board_size):
            print(chr(ord("A") + row), end=" ")
            for col in range(self.board_size):
                ch = self.board[row][col]
                if ch == 0:
                    print(".", end=" ")
                elif ch == 1:
                    print("X", end=" ")
                elif ch == -1:
                    print("O", end=" ")
            print()

    def human_input_to_action(self):
        human_input = input("Enter an action: ")
        if (
            len(human_input) == 2
            and human_input[0] in self.board_markers
            and human_input[1] in self.board_markers
        ):
            x = ord(human_input[0]) - 65
            y = ord(human_input[1]) - 65
            if self.board[x][y] == 0:
                return True, x * self.board_size + y
        return False, -1

    def action_to_human_input(self, action):
        x = math.floor(action / self.board_size)
        y = action % self.board_size
        x = chr(x + 65)
        y = chr(y + 65)
        return x + y
