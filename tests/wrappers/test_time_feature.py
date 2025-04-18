import gymnasium as gym
import numpy as np
import pytest
from gymnasium import spaces
from stable_baselines3.common.env_checker import check_env
from stable_baselines3.common.envs import BitFlippingEnv

from sb3_contrib.common.wrappers import TimeFeatureWrapper


class CustomGoalEnv(gym.Env):
    def __init__(self):
        super().__init__()
        self.observation_space = spaces.Dict(
            {
                "observation": spaces.Box(low=-np.inf, high=np.inf, shape=(3,)),
                "achieved_goal": spaces.Box(low=-np.inf, high=np.inf, shape=(3,)),
                "desired_goal": spaces.Box(low=-np.inf, high=np.inf, shape=(3,)),
            }
        )
        self.action_space = spaces.Box(low=-1, high=1, shape=(5,), dtype=np.float32)

    def reset(self):
        return self.observation_space.sample(), {}

    def step(self, action):
        return self.observation_space.sample(), 0.0, False, False, {}


def check_time_feature(obs, timestep, max_timesteps):
    assert np.allclose(obs[-1], 1.0 - timestep / max_timesteps)


def test_time_feature():
    env = gym.make("Pendulum-v1")
    env = TimeFeatureWrapper(env)
    check_env(env, warn=False)
    # Check for four episodes
    max_timesteps = 200
    obs, _ = env.reset()
    for _ in range(4):
        done = False
        check_time_feature(obs, timestep=0, max_timesteps=max_timesteps)
        for step in range(1, max_timesteps + 1):
            obs, _, terminated, truncated, _ = env.step(env.action_space.sample())
            check_time_feature(obs, timestep=step, max_timesteps=max_timesteps)
            done = terminated or truncated
        if done:
            obs, _ = env.reset()

    env = BitFlippingEnv()
    with pytest.raises(AssertionError):
        env = TimeFeatureWrapper(env)

    env = CustomGoalEnv()
    env = TimeFeatureWrapper(env, max_steps=500)
    obs, _ = env.reset()
    check_time_feature(obs["observation"], timestep=0, max_timesteps=500)
    obs = env.step(env.action_space.sample())[0]
    check_time_feature(obs["observation"], timestep=1, max_timesteps=500)

    # In test mode, the time feature must be constant
    env = gym.make("Pendulum-v1")
    env = TimeFeatureWrapper(env, test_mode=True)
    obs, _ = env.reset()
    check_time_feature(obs, timestep=0, max_timesteps=200)
    obs = env.step(env.action_space.sample())[0]
    # Should be the same
    check_time_feature(obs, timestep=0, max_timesteps=200)
