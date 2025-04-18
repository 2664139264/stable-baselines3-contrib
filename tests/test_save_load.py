import os
import pathlib
from collections import OrderedDict
from copy import deepcopy

import gymnasium as gym
import numpy as np
import pytest
import torch as th
from stable_baselines3.common.base_class import BaseAlgorithm
from stable_baselines3.common.envs import FakeImageEnv, IdentityEnv, IdentityEnvBox
from stable_baselines3.common.utils import get_device
from stable_baselines3.common.vec_env import DummyVecEnv

from sb3_contrib import ARS, QRDQN, TQC, TRPO

MODEL_LIST = [ARS, QRDQN, TQC, TRPO]


def select_env(model_class: BaseAlgorithm) -> gym.Env:
    """
    Selects an environment with the correct action space as QRDQN only supports discrete action space
    """
    if model_class == QRDQN:
        return IdentityEnv(10)
    else:
        return IdentityEnvBox(-10, 10)


@pytest.mark.parametrize("model_class", MODEL_LIST)
def test_save_load(tmp_path, model_class):
    """
    Test if 'save' and 'load' saves and loads model correctly
    and if 'get_parameters' and 'set_parameters' and work correctly.

    ''warning does not test function of optimizer parameter load

    :param model_class: (BaseAlgorithm) A RL model
    """

    env = DummyVecEnv([lambda: select_env(model_class)])

    policy_kwargs = dict(net_arch=[16])

    if model_class in {QRDQN, TQC}:
        policy_kwargs.update(dict(n_quantiles=20))

    # create model
    model = model_class("MlpPolicy", env, verbose=1, policy_kwargs=policy_kwargs)
    model.learn(total_timesteps=300)

    env.reset()
    observations = np.concatenate([env.step([env.action_space.sample()])[0] for _ in range(10)], axis=0)

    # Get parameters of different objects
    # deepcopy to avoid referencing to tensors we are about to modify
    original_params = deepcopy(model.get_parameters())

    # Test different error cases of set_parameters.
    # Test that invalid object names throw errors
    invalid_object_params = deepcopy(original_params)
    invalid_object_params["I_should_not_be_a_valid_object"] = "and_I_am_an_invalid_tensor"
    with pytest.raises(ValueError):
        model.set_parameters(invalid_object_params, exact_match=True)
    with pytest.raises(ValueError):
        model.set_parameters(invalid_object_params, exact_match=False)

    # Test that exact_match catches when something was missed.
    missing_object_params = {k: v for k, v in list(original_params.items())[:-1]}
    with pytest.raises(ValueError):
        model.set_parameters(missing_object_params, exact_match=True)

    # Test that exact_match catches when something inside state-dict
    # is missing but we have exact_match.
    missing_state_dict_tensor_params = {}
    for object_name in original_params:
        object_params = {}
        missing_state_dict_tensor_params[object_name] = object_params
        # Skip last item in state-dict
        for k, v in list(original_params[object_name].items())[:-1]:
            object_params[k] = v
    with pytest.raises(RuntimeError):
        # PyTorch load_state_dict throws RuntimeError if strict but
        # invalid state-dict.
        model.set_parameters(missing_state_dict_tensor_params, exact_match=True)

    # Test that parameters do indeed change.
    random_params = {}
    for object_name, params in original_params.items():
        # Do not randomize optimizer parameters (custom layout)
        if "optim" in object_name:
            random_params[object_name] = params
        else:
            # Again, skip the last item in state-dict
            random_params[object_name] = OrderedDict(
                (param_name, th.rand_like(param)) for param_name, param in list(params.items())[:-1]
            )

    # Update model parameters with the new random values
    model.set_parameters(random_params, exact_match=False)

    new_params = model.get_parameters()
    # Check that all params except the final item in each state-dict are different.
    for object_name in original_params:
        # Skip optimizers (no valid comparison with just th.allclose)
        if "optim" in object_name:
            continue
        # state-dicts use ordered dictionaries, so key order
        # is guaranteed.
        last_key = list(original_params[object_name].keys())[-1]
        for k in original_params[object_name]:
            if k == last_key:
                # Should be same as before
                assert th.allclose(
                    original_params[object_name][k], new_params[object_name][k]
                ), "Parameter changed despite not included in the loaded parameters."
            else:
                # Should be different
                assert not th.allclose(
                    original_params[object_name][k], new_params[object_name][k]
                ), "Parameters did not change as expected."

    params = new_params

    # get selected actions
    selected_actions, _ = model.predict(observations, deterministic=True)

    # Check
    model.save(tmp_path / "test_save.zip")
    del model

    # Check if the model loads as expected for every possible choice of device:
    for device in ["auto", "cpu", "cuda"]:
        model = model_class.load(str(tmp_path / "test_save.zip"), env=env, device=device)

        # check if the model was loaded to the correct device
        assert model.device.type == get_device(device).type
        assert model.policy.device.type == get_device(device).type

        # check if params are still the same after load
        new_params = model.get_parameters()

        # Check that all params are the same as before save load procedure now
        for object_name in new_params:
            # Skip optimizers (no valid comparison with just th.allclose)
            if "optim" in object_name:
                continue
            for key in params[object_name]:
                assert new_params[object_name][key].device.type == get_device(device).type
                assert th.allclose(
                    params[object_name][key].to("cpu"), new_params[object_name][key].to("cpu")
                ), "Model parameters not the same after save and load."

        # check if model still selects the same actions
        new_selected_actions, _ = model.predict(observations, deterministic=True)
        assert np.allclose(selected_actions, new_selected_actions, 1e-4)

        # check if learn still works
        model.learn(total_timesteps=300)

        del model

    # clear file from os
    os.remove(tmp_path / "test_save.zip")


@pytest.mark.parametrize("model_class", MODEL_LIST)
def test_set_env(model_class):
    """
    Test if set_env function does work correct
    :param model_class: (BaseAlgorithm) A RL model
    """

    # use discrete for QRDQN
    env = DummyVecEnv([lambda: select_env(model_class)])
    env2 = DummyVecEnv([lambda: select_env(model_class)])
    env3 = select_env(model_class)

    kwargs = dict(policy_kwargs=dict(net_arch=[16]))
    if model_class in {TQC, QRDQN}:
        kwargs.update(dict(learning_starts=100))
        kwargs["policy_kwargs"].update(dict(n_quantiles=20))

    # create model
    model = model_class("MlpPolicy", env, **kwargs)
    # learn
    model.learn(total_timesteps=150)

    # change env
    model.set_env(env2)
    # learn again
    model.learn(total_timesteps=150)

    # change env test wrapping
    model.set_env(env3)
    # learn again
    model.learn(total_timesteps=150)


@pytest.mark.parametrize("model_class", MODEL_LIST)
def test_exclude_include_saved_params(tmp_path, model_class):
    """
    Test if exclude and include parameters of save() work

    :param model_class: (BaseAlgorithm) A RL model
    """
    env = DummyVecEnv([lambda: select_env(model_class)])

    # create model, set verbose as 2, which is not standard
    model = model_class("MlpPolicy", env, policy_kwargs=dict(net_arch=[16]), verbose=2)

    # Check if exclude works
    model.save(tmp_path / "test_save", exclude=["verbose"])
    del model
    model = model_class.load(str(tmp_path / "test_save.zip"))
    # check if verbose was not saved
    assert model.verbose != 2

    # set verbose as something different then standard settings
    model.verbose = 2
    # Check if include works
    model.save(tmp_path / "test_save", exclude=["verbose"], include=["verbose"])
    del model
    model = model_class.load(str(tmp_path / "test_save.zip"))
    assert model.verbose == 2

    # clear file from os
    os.remove(tmp_path / "test_save.zip")


@pytest.mark.parametrize("model_class", [TQC, QRDQN])
def test_save_load_replay_buffer(tmp_path, model_class):
    path = pathlib.Path(tmp_path / "logs/replay_buffer.pkl")
    path.parent.mkdir(exist_ok=True, parents=True)  # to not raise a warning
    model = model_class("MlpPolicy", select_env(model_class), buffer_size=1000)
    model.learn(300)
    old_replay_buffer = deepcopy(model.replay_buffer)
    model.save_replay_buffer(path)
    model.replay_buffer = None
    model.load_replay_buffer(path)

    assert np.allclose(old_replay_buffer.observations, model.replay_buffer.observations)
    assert np.allclose(old_replay_buffer.actions, model.replay_buffer.actions)
    assert np.allclose(old_replay_buffer.rewards, model.replay_buffer.rewards)
    assert np.allclose(old_replay_buffer.dones, model.replay_buffer.dones)
    infos = [[{"TimeLimit.truncated": truncated}] for truncated in old_replay_buffer.timeouts]

    # test extending replay buffer
    model.replay_buffer.extend(
        old_replay_buffer.observations,
        old_replay_buffer.observations,
        old_replay_buffer.actions,
        old_replay_buffer.rewards,
        old_replay_buffer.dones,
        infos,
    )


@pytest.mark.parametrize("model_class", MODEL_LIST)
@pytest.mark.parametrize("policy_str", ["MlpPolicy", "CnnPolicy"])
def test_save_load_policy(tmp_path, model_class, policy_str):
    """
    Test saving and loading policy only.

    :param model_class: (BaseAlgorithm) A RL model
    :param policy_str: (str) Name of the policy.
    """
    kwargs = dict(policy_kwargs=dict(net_arch=[16]))

    if policy_str == "CnnPolicy" and model_class is ARS:
        pytest.skip("ARS does not support CnnPolicy")

    if policy_str == "MlpPolicy":
        env = select_env(model_class)
    else:
        if model_class in [TQC, QRDQN]:
            # Avoid memory error when using replay buffer
            # Reduce the size of the features
            kwargs = dict(
                buffer_size=250,
                learning_starts=100,
                policy_kwargs=dict(features_extractor_kwargs=dict(features_dim=32)),
            )
        else:
            kwargs = dict(
                n_steps=128,
                policy_kwargs=dict(features_extractor_kwargs=dict(features_dim=32)),
            )
        env = FakeImageEnv(screen_height=40, screen_width=40, n_channels=2, discrete=model_class == QRDQN)

    # Reduce number of quantiles for faster tests
    if model_class in [TQC, QRDQN]:
        kwargs["policy_kwargs"].update(dict(n_quantiles=20))

    env = DummyVecEnv([lambda: env])

    # create model
    model = model_class(policy_str, env, verbose=1, **kwargs)
    model.learn(total_timesteps=300)

    env.reset()
    observations = np.concatenate([env.step([env.action_space.sample()])[0] for _ in range(10)], axis=0)

    policy = model.policy
    policy_class = policy.__class__
    actor, actor_class = None, None
    if model_class in [TQC]:
        actor = policy.actor
        actor_class = actor.__class__

    # Get dictionary of current parameters
    params = deepcopy(policy.state_dict())

    # Modify all parameters to be random values
    random_params = {param_name: th.rand_like(param) for param_name, param in params.items()}

    # Update model parameters with the new random values
    policy.load_state_dict(random_params)

    new_params = policy.state_dict()
    # Check that all params are different now
    for k in params:
        assert not th.allclose(params[k], new_params[k]), "Parameters did not change as expected."

    params = new_params

    # get selected actions
    selected_actions, _ = policy.predict(observations, deterministic=True)
    # Should also work with the actor only
    if actor is not None:
        selected_actions_actor, _ = actor.predict(observations, deterministic=True)

    # Save and load policy
    policy.save(tmp_path / "policy.pkl")
    # Save and load actor
    if actor is not None:
        actor.save(tmp_path / "actor.pkl")

    device = policy.device

    del policy, actor

    policy = policy_class.load(tmp_path / "policy.pkl").to(device)
    if actor_class is not None:
        actor = actor_class.load(tmp_path / "actor.pkl")

    # check if params are still the same after load
    new_params = policy.state_dict()

    # Check that all params are the same as before save load procedure now
    for key in params:
        assert th.allclose(params[key], new_params[key]), "Policy parameters not the same after save and load."

    # check if model still selects the same actions
    new_selected_actions, _ = policy.predict(observations, deterministic=True)
    assert np.allclose(selected_actions, new_selected_actions, 1e-4)

    if actor_class is not None:
        new_selected_actions_actor, _ = actor.predict(observations, deterministic=True)
        assert np.allclose(selected_actions_actor, new_selected_actions_actor, 1e-4)
        assert np.allclose(selected_actions_actor, new_selected_actions, 1e-4)

    # clear file from os
    os.remove(tmp_path / "policy.pkl")
    if actor_class is not None:
        os.remove(tmp_path / "actor.pkl")


@pytest.mark.parametrize("model_class", [QRDQN])
@pytest.mark.parametrize("policy_str", ["MlpPolicy", "CnnPolicy"])
def test_save_load_q_net(tmp_path, model_class, policy_str):
    """
    Test saving and loading q-network/quantile net only.

    :param model_class: (BaseAlgorithm) A RL model
    :param policy_str: (str) Name of the policy.
    """
    kwargs = dict(policy_kwargs=dict(net_arch=[16]))
    if policy_str == "MlpPolicy":
        env = select_env(model_class)
    else:
        if model_class in [QRDQN]:
            # Avoid memory error when using replay buffer
            # Reduce the size of the features
            kwargs = dict(
                buffer_size=250,
                learning_starts=100,
                policy_kwargs=dict(features_extractor_kwargs=dict(features_dim=32)),
            )
        env = FakeImageEnv(screen_height=40, screen_width=40, n_channels=2, discrete=model_class == QRDQN)

    # Reduce number of quantiles for faster tests
    if model_class in [QRDQN]:
        kwargs["policy_kwargs"].update(dict(n_quantiles=20))

    env = DummyVecEnv([lambda: env])

    # create model
    model = model_class(policy_str, env, verbose=1, **kwargs)
    model.learn(total_timesteps=300)

    env.reset()
    observations = np.concatenate([env.step([env.action_space.sample()])[0] for _ in range(10)], axis=0)

    q_net = model.quantile_net
    q_net_class = q_net.__class__

    # Get dictionary of current parameters
    params = deepcopy(q_net.state_dict())

    # Modify all parameters to be random values
    random_params = {param_name: th.rand_like(param) for param_name, param in params.items()}

    # Update model parameters with the new random values
    q_net.load_state_dict(random_params)

    new_params = q_net.state_dict()
    # Check that all params are different now
    for k in params:
        assert not th.allclose(params[k], new_params[k]), "Parameters did not change as expected."

    params = new_params

    # get selected actions
    selected_actions, _ = q_net.predict(observations, deterministic=True)

    # Save and load q_net
    q_net.save(tmp_path / "q_net.pkl")

    del q_net

    q_net = q_net_class.load(tmp_path / "q_net.pkl")

    # check if params are still the same after load
    new_params = q_net.state_dict()

    # Check that all params are the same as before save load procedure now
    for key in params:
        assert th.allclose(params[key], new_params[key]), "Policy parameters not the same after save and load."

    # check if model still selects the same actions
    new_selected_actions, _ = q_net.predict(observations, deterministic=True)
    assert np.allclose(selected_actions, new_selected_actions, 1e-4)

    # clear file from os
    os.remove(tmp_path / "q_net.pkl")


def test_save_load_pytorch_var(tmp_path):
    model = TQC("MlpPolicy", "Pendulum-v1", seed=3, policy_kwargs=dict(net_arch=[64], n_critics=1))
    model.learn(200)
    save_path = str(tmp_path / "tqc_pendulum")
    model.save(save_path)
    env = model.get_env()
    log_ent_coef_before = model.log_ent_coef

    del model

    model = TQC.load(save_path, env=env)
    assert th.allclose(log_ent_coef_before, model.log_ent_coef)
    model.learn(200)
    log_ent_coef_after = model.log_ent_coef
    # Check that the entropy coefficient is still optimized
    assert not th.allclose(log_ent_coef_before, log_ent_coef_after)

    # With a fixed entropy coef
    model = TQC("MlpPolicy", "Pendulum-v1", seed=3, ent_coef=0.01, policy_kwargs=dict(net_arch=[64], n_critics=1))
    model.learn(200)
    save_path = str(tmp_path / "tqc_pendulum")
    model.save(save_path)
    env = model.get_env()
    assert model.log_ent_coef is None
    ent_coef_before = model.ent_coef_tensor

    del model

    model = TQC.load(save_path, env=env)
    assert th.allclose(ent_coef_before, model.ent_coef_tensor)
    model.learn(200)
    ent_coef_after = model.ent_coef_tensor
    assert model.log_ent_coef is None
    # Check that the entropy coefficient is still the same
    assert th.allclose(ent_coef_before, ent_coef_after)
