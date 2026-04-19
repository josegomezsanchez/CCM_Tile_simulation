import simpy

def example(env):
    n=1
    value = yield env.timeout(5,value = n+1)
    print("now= %d, value=%s" % (env.now, value))

env = simpy.Environment()
p=env.process(example(env))
env.run()

