import copy
import casadi as C
import matplotlib.pyplot as plt
import numpy
from numpy import pi
import zmq
import pickle

import crosswind_collocation
from collocation import Coll,boundsFeedback,trajectory
from config import readConfig
import kiteutils
import kite_pb2
import kiteproto
import models

def setupOcp(dae,conf,publisher,nk=50,nicp=1,deg=4):
    ocp = Coll(dae, nk=nk,nicp=nicp,deg=deg)
    
    print "setting up collocation..."
    ocp.setupCollocation(ocp.lookup('endTime'))
    
    # constrain invariants
    def constrainInvariantErrs():
        dcm = ocp.lookup('dcm',timestep=0)
        err = C.mul(dcm.T,dcm)
        ocp.constrain( C.veccat([err[0,0] - 1, err[1,1]-1, err[2,2] - 1, err[0,1], err[0,2], err[1,2]]), '==', 0)
        ocp.constrain(ocp.lookup('c',timestep=0), '==', 0)
        ocp.constrain(ocp.lookup('cdot',timestep=0), '==', 0)
    constrainInvariantErrs()

    # constrain line angle
    for k in range(0,nk+1):
        ocp.constrain(kiteutils.getCosLineAngle(ocp,k),'>=',C.cos(55*pi/180))

    # constrain airspeed
    def constrainAirspeedAlphaBeta():
        for k in range(0,nk):
            ocp.constrain(ocp.lookup('airspeed',timestep=k), '>=', 1)
            ocp.constrainBnds(ocp.lookup('alpha(deg)',timestep=k), (-5,10))
            ocp.constrainBnds(ocp.lookup('beta(deg)', timestep=k), (-10,10))
    constrainAirspeedAlphaBeta()

    # constrain tether force
    for k in range(nk):
        ocp.constrain( ocp.lookup('tether tension',timestep=k,degIdx=1), '>=', 0)
        ocp.constrain( ocp.lookup('tether tension',timestep=k,degIdx=ocp.deg), '>=', 0)

    # make it periodic
    for name in [ "y","z",
                  "dy","dz",
                  "w1","w2","w3",
                  "r","dr",
                  'aileron','elevator'
                  ]:
        ocp.constrain(ocp.lookup(name,timestep=0),'==',ocp.lookup(name,timestep=-1))

    # periodic attitude
#    kiteutils.periodicEulers(ocp)
#    kiteutils.periodicOrthonormalizedDcm(ocp)
    kiteutils.periodicDcm(ocp)

    # bounds
    ocp.bound('aileron',(-0.04,0.04))
    ocp.bound('elevator',(-0.1,0.1))
    ocp.bound('daileron',(-2.0,2.0))
    ocp.bound('delevator',(-2.0,2.0))

    ocp.bound('x',(-200,200))
    ocp.bound('y',(-200,200))
    if 'minAltitude' in conf:
        ocp.bound('z',(conf['minAltitude'],200))
    else:
        ocp.bound('z',(0.5,200))
    ocp.bound('r',(1,200))
    ocp.bound('dr',(-30,30))
    ocp.bound('ddr',(-500,500))

    for e in ['e11','e21','e31','e12','e22','e32','e13','e23','e33']:
        ocp.bound(e,(-1.1,1.1))

    for d in ['dx','dy','dz']:
        ocp.bound(d,(-70,70))

    for w in ['w1','w2','w3']:
        ocp.bound(w,(-4*pi,4*pi))

    ocp.bound('endTime',(1.8,1.8))
    ocp.guess('endTime',1.8)
    ocp.bound('w0',(10,10))
    ocp.bound('energy',(-1e6,1e6))

    # boundary conditions
    ocp.bound('energy',(0,0),timestep=0,quiet=True)
    ocp.bound('y',(0,0),timestep=0,quiet=True)
    
    return ocp


if __name__=='__main__':
    context   = zmq.Context(1)
    publisher = context.socket(zmq.PUB)
    publisher.bind("tcp://*:5563")

    print "reading config..."
    conf = readConfig('config.ini','configspec.ini')
    conf['runHomotopy'] = True
    conf['minAltitude'] = 0.5
    
    print "creating model..."
    dae = models.crosswind(conf,extraParams=['endTime'])

    print "setting up ocp..."
    ocp = setupOcp(dae,conf,publisher,nk=40)

    lineRadiusGuess = 70.0
    circleRadiusGuess = 10.0

    # trajectory for homotopy
    homotopyTraj = {'x':[],'y':[],'z':[]}
    for k in range(ocp.nk+1):
        r = circleRadiusGuess
        h = numpy.sqrt(lineRadiusGuess**2 - r**2)
        nTurns = 1
        
        # path following
        theta = nTurns*2*pi*k/float(ocp.nk)
        thetaDot = nTurns*2*pi/(float(ocp.nk)*ocp._guess.lookup('endTime'))
        xyzCircleFrame    = numpy.array([h, r*numpy.sin(theta),          -r*numpy.cos(theta)])
        xyzDotCircleFrame = numpy.array([0, r*numpy.cos(theta)*thetaDot,  r*numpy.sin(theta)*thetaDot])

        phi = numpy.arcsin(r/lineRadiusGuess) # rotate so it's above ground
        phi += numpy.arcsin((conf['minAltitude']+0.3)/lineRadiusGuess)
        R_c2n = numpy.matrix([[ numpy.cos(phi), 0, -numpy.sin(phi)],
                              [              0, 1,               0],
                              [ numpy.sin(phi), 0,  numpy.cos(phi)]])
        xyz    = numpy.dot(R_c2n, xyzCircleFrame)
        xyzDot = numpy.dot(R_c2n, xyzDotCircleFrame)

        homotopyTraj['x'].append(float(xyz[0,0]))
        homotopyTraj['y'].append(float(xyz[0,1]))
        homotopyTraj['z'].append(float(xyz[0,2]))

        x = float(xyz[0,0])
        y = float(xyz[0,1])
        z = float(xyz[0,2])
        
        dx = float(xyzDot[0,0])
        dy = float(xyzDot[0,1])
        dz = float(xyzDot[0,2])
        
        ocp.guess('x',x,timestep=k)
        ocp.guess('y',y,timestep=k)
        ocp.guess('z',z,timestep=k)
        ocp.guess('dx',dx,timestep=k)
        ocp.guess('dy',dy,timestep=k)
        ocp.guess('dz',dz,timestep=k)

        p0 = numpy.array([x,y,z])
        dp0 = numpy.array([dx,dy,dz])
        e1 = dp0/numpy.linalg.norm(dp0)
        e3 = p0/lineRadiusGuess
        e2 = numpy.cross(e3,e1)

        ocp.guess('e11',e1[0],timestep=k)
        ocp.guess('e12',e1[1],timestep=k)
        ocp.guess('e13',e1[2],timestep=k)
        
        ocp.guess('e21',e2[0],timestep=k)
        ocp.guess('e22',e2[1],timestep=k)
        ocp.guess('e23',e2[2],timestep=k)

        ocp.guess('e31',e3[0],timestep=k)
        ocp.guess('e32',e3[1],timestep=k)
        ocp.guess('e33',e3[2],timestep=k)
        
    
    # objective function
    obj = -1e6*ocp.lookup('gamma_homotopy')
    for k in range(ocp.nk+1):
        obj += (homotopyTraj['x'][k] - ocp.lookup('x',timestep=k))**2
        obj += (homotopyTraj['y'][k] - ocp.lookup('y',timestep=k))**2
        obj += (homotopyTraj['z'][k] - ocp.lookup('z',timestep=k))**2

    # control regularization
    for k in range(ocp.nk):
        ddr = ocp.lookup('ddr',timestep=k)
        daileron = ocp.lookup('daileron',timestep=k)
        delevator = ocp.lookup('delevator',timestep=k)
        
        daileronSigma = 0.1
        delevatorSigma = 0.1
        ddrSigma = 1.0
        
        ailObj = daileron*daileron / (daileronSigma*daileronSigma)
        eleObj = delevator*delevator / (delevatorSigma*delevatorSigma)
        winchObj = ddr*ddr / (ddrSigma*ddrSigma)
        
        obj += 1e-2*(ailObj + eleObj + winchObj)/float(ocp.nk)

    # homotopy forces/torques regularization
    homoReg = 0
    for k in range(ocp.nk):
        homoReg += ocp.lookup('f1_homotopy',timestep=k)**2
        homoReg += ocp.lookup('f2_homotopy',timestep=k)**2
        homoReg += ocp.lookup('f3_homotopy',timestep=k)**2
        homoReg += ocp.lookup('t1_homotopy',timestep=k)**2
        homoReg += ocp.lookup('t2_homotopy',timestep=k)**2
        homoReg += ocp.lookup('t3_homotopy',timestep=k)**2
    obj += 1e-2*homoReg/float(ocp.nk)

    ocp.setObjective( obj )

    # initial guesses
    ocp.guess('w0',10)
    ocp.guess('r',lineRadiusGuess)
    
    for name in ['w1','w2','w3','dr','ddr','aileron','elevator','daileron','delevator']:
        ocp.guess(name,0)

    # homotopy forces/torques bounds/guess
    for k in [1,2,3]:
        f = 'f'+str(k)+'_homotopy'
        t = 't'+str(k)+'_homotopy'
        ocp.guess(f,0)
        ocp.guess(t,0)

        ocp.bound(f,(-1e4,1e4))
        ocp.bound(t,(-1e4,1e4))
        
    ocp.guess('gamma_homotopy',0)
        
    # callback function
    class MyCallback:
        def __init__(self):
            self.iter = 0 
        def __call__(self,f,*args):
            self.iter = self.iter + 1
            xOpt = numpy.array(f.input(C.NLP_X_OPT))

            traj = trajectory.Trajectory(ocp,xOpt)
            
            kiteProtos = []
            for k in range(0,ocp.nk):
                for nicpIdx in range(0,ocp.nicp):
                    for j in [0]:
#                    for j in range(ocp.deg+1):
                        kiteProtos.append( kiteproto.toKiteProto(C.DMatrix(traj.dvMap.xVec(k,nicpIdx=nicpIdx,degIdx=j)),
                                                                 C.DMatrix(traj.dvMap.uVec(k)),
                                                                 C.DMatrix(traj.dvMap.pVec()),
                                                                 conf['kite']['zt'],
                                                                 conf['carousel']['rArm'],
                                                                 lineAlpha=0.2,
                                                                 zeroDelta=True) )
            mc = kite_pb2.MultiCarousel()
            mc.css.extend(list(kiteProtos))
            
            mc.messages.append("w0: "+str(traj.lookup('w0')))
            mc.messages.append("iter: "+str(self.iter))
            mc.messages.append("endTime: "+str(traj.lookup('endTime')))
            mc.messages.append("average power: "+str(traj.lookup('energy',timestep=-1)/traj.lookup('endTime'))+" W")
            mc.messages.append("homotopy gamma: "+str(traj.lookup('gamma_homotopy')))

            # bounds feedback
#            lbx = ocp.solver.input(C.NLP_LBX)
#            ubx = ocp.solver.input(C.NLP_UBX)
#            violations = boundsFeedback(xOpt,lbx,ubx,ocp.bndtags,tolerance=1e-9)
#            for name in violations:
#                violmsg = "violation!: "+name+": "+str(violations[name])
#                mc.messages.append(violmsg)
            
            publisher.send_multipart(["multi-carousel", mc.SerializeToString()])

    # solver
    solverOptions = [ ("expand_f",True)
                    , ("expand_g",True)
                    , ("generate_hessian",True)
#                     ,("qp_solver",C.NLPQPSolver)
#                     ,("qp_solver_options",{'nlp_solver': C.IpoptSolver, "nlp_solver_options":{"linear_solver":"ma57"}})
                    , ("linear_solver","ma57")
                    , ("max_iter",1000)
                    , ("tol",1e-4)
#                    , ('monitor',['eval_g'])
#                    , ("Timeout", 1e6)
#                    , ("UserHM", True)
#                    , ("ScaleConIter",True)
#                    , ("ScaledFD",True)
#                    , ("ScaledKKT",True)
#                    , ("ScaledObj",True)
#                    , ("ScaledQP",True)
                    ]
    
    print "setting up solver..."
    ocp.setupSolver( solverOpts=solverOptions,
                     callback=MyCallback() )
    ocp.guess('energy',0)

    xInit = None
    ocp.bound('gamma_homotopy',(1e-4,1e-4),force=True)
    traj = ocp.solve(xInit=xInit)

    ocp.bound('gamma_homotopy',(0,1),force=True)
    traj = ocp.solve(xInit=traj.getDvs())
    
    ocp.bound('gamma_homotopy',(1,1),force=True)
    ocp.bound('endTime',(4.0,4.0),force=True)
    traj = ocp.solve(xInit=traj.getDvs())

    print "saving optimal trajectory"
    traj.save("data/crosswind_homotopy.dat")

    def printBoundsFeedback():
#        (_,lbx,ubx) = ocp._vectorizeBoundsAndGuess( ocp._parseBoundsAndGuess(False,False) )
        lbx = ocp.solver.input(C.NLP_LBX)
        ubx = ocp.solver.input(C.NLP_UBX)
        violations = boundsFeedback(opt['X_OPT'],lbx,ubx,ocp.bndtags,tolerance=0.5)
        for name in violations:
            print "violation!: "+name+": "+str(violations[name])
#    printBoundsFeedback()

    # Plot the results
    def plotResults():
        traj.subplot(['f1_homotopy','f2_homotopy','f3_homotopy'])
        traj.subplot(['t1_homotopy','t2_homotopy','t3_homotopy'])
        traj.subplot(['x','y','z'])
        traj.subplot(['dx','dy','dz'])
        traj.subplot([['aileron','elevator'],['daileron','delevator']],title='control surfaces')
        traj.subplot(['r','dr','ddr'])
        traj.subplot(['wind at altitude','dr','dx'])
        traj.subplot(['c','cdot','cddot'],title="invariants")
        traj.plot('airspeed')
        traj.subplot([['alpha(deg)','alphaTail(deg)'],['beta(deg)','betaTail(deg)']])
        traj.subplot(['cL','cD','L/D'])
        traj.subplot(['winch power', 'tether tension'])
        traj.plot('energy')
        traj.subplot(['w1','w2','w3'])
        traj.subplot(['e11','e12','e13','e21','e22','e23','e31','e32','e33'])
        plt.show()
    plotResults()