import pytest
from hydra.experimental import initialize_config_module, compose

from maze.maze_cli import maze_run
from maze.test.shared_test_utils.rollout_utils import run_rollout


def run_behavioral_cloning(env: str, teacher_policy: str, bc_runner: str, bc_wrappers: str, bc_model: str):
    """Run behavioral cloning for given config parameters.

    Runs a rollout with the given teacher_policy, then runs behavioral cloning on the collected trajectory data.
    """
    # Heuristics rollout
    rollout_config = dict(configuration="test",
                          env=env,
                          policy=teacher_policy,
                          runner="sequential")
    run_rollout(rollout_config)

    # Behavioral cloning on top of the heuristic rollout trajectories
    train_config = dict(configuration="test", env=env, wrappers=bc_wrappers,
                        model=bc_model, algorithm="bc", runner=bc_runner)
    with initialize_config_module(config_module="maze.conf"):
        cfg = compose(config_name="conf_train", overrides=[key + "=" + value for key, value in train_config.items()])
        maze_run(cfg)

    # Note: The log might output statistics multiple times -- this is caused by stats log writers being
    #       registered repeatedly in each maze_run method above (does not happen in normal scenario)


@pytest.mark.parametrize("runner", ["dev", "local"])
def test_behavioral_cloning(runner: str):
    """Rolls out a heuristic policy on Cutting 2D env and collects trajectories, then runs
    behavioral cloning on the collected trajectory data."""
    run_behavioral_cloning(env="gym_env", teacher_policy="random_policy",
                           bc_runner=runner, bc_wrappers="vector_obs", bc_model="flatten_concat")