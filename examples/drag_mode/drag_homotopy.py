# Copyright 2012-2013 Greg Horn
#
# This file is part of rawesome.
#
# rawesome is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# rawesome is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with rawesome.  If not, see <http://www.gnu.org/licenses/>.

import copy
import casadi as C
import matplotlib.pyplot as plt
import numpy
from numpy import pi
import pickle

import rawe
import rawekite
import drag_dae
from autogen.todragProto import toProto
from autogen.drag_pb2 import Trajectory

def setupOcp(dae,conf,nk,nicp=1,deg=4):
    ocp = rawe.collocation.Coll(dae, nk=nk,nicp=nicp,deg=deg)

    print "setting up collocation..."
    ocp.setupCollocation(ocp.lookup('endTime'))

    # constrain invariants
    def constrainInvariantErrs():
        R_n2b = ocp.lookup('R_n2b',timestep=0)
        rawekite.kiteutils.makeOrthonormal(ocp, R_n2b)
        ocp.constrain(ocp.lookup('c',timestep=0), '==', 0, tag=('initial c 0',None))
        ocp.constrain(ocp.lookup('cdot',timestep=0), '==', 0, tag=('initial cdot 0',None))
    constrainInvariantErrs()

    # constrain line angle
    for k in range(0,nk):
        ocp.constrain(ocp.lookup('cos_line_angle',timestep=k),'>=',C.cos(55*pi/180), tag=('line angle',k))

    # constrain airspeed
    def constrainAirspeedAlphaBeta():
        for k in range(0,nk):
            for j in range(0,deg+1):
                ocp.constrain(ocp.lookup('airspeed',timestep=k,degIdx=j), '>=', 20, tag=('airspeed',nk))
                ocp.constrainBnds(ocp.lookup('alpha_deg',timestep=k,degIdx=j), (-4.5,8.5), tag=('alpha',nk))
                ocp.constrainBnds(ocp.lookup('beta_deg', timestep=k,degIdx=j), (-10,10), tag=('beta',nk))
    constrainAirspeedAlphaBeta()

    # constrain tether force
    for k in range(nk):
        ocp.constrain( ocp.lookup('tether_tension',timestep=k,degIdx=1), '>=', 0, tag=('tether tension',(nk,0)))
        ocp.constrain( ocp.lookup('tether_tension',timestep=k,degIdx=ocp.deg), '>=', 0, tag=('tether tension',(nk,1)))

    # make it periodic
    for name in [ "r_n2b_n_y","r_n2b_n_z",
                  "v_bn_n_y","v_bn_n_z",
                  "w_bn_b_x","w_bn_b_y","w_bn_b_z",
                  'aileron','elevator','rudder','flaps','prop_drag'
                  ]:
        ocp.constrain(ocp.lookup(name,timestep=0),'==',ocp.lookup(name,timestep=-1), tag=('periodic '+name,None))

    # periodic attitude
    rawekite.kiteutils.periodicDcm(ocp)

    # bounds
    ocp.bound('aileron', (numpy.radians(-10),numpy.radians(10)))
    ocp.bound('elevator',(numpy.radians(-10),numpy.radians(10)))
    ocp.bound('rudder',  (numpy.radians(-10),numpy.radians(10)))
    ocp.bound('flaps',  (numpy.radians(0),numpy.radians(0)))
    ocp.bound('prop_drag',  (-1e3, 1e3))
    # can't bound flaps==0 AND have periodic flaps at the same time
    # bounding flaps (-1,1) at timestep 0 doesn't really free them, but satisfies LICQ
    ocp.bound('flaps', (-1,1),timestep=0,quiet=True)
    ocp.bound('daileron',(-2.0,2.0))
    ocp.bound('delevator',(-2.0,2.0))
    ocp.bound('drudder',(-2.0,2.0))
    ocp.bound('dflaps',(-2.0,2.0))
    ocp.bound('dprop_drag',  (-1e3, 1e3))

    ocp.bound('r_n2b_n_x',(-2000,2000))
    ocp.bound('r_n2b_n_y',(-2000,2000))
    if 'minAltitude' in conf:
        ocp.bound('r_n2b_n_z',(-2000, -conf['minAltitude']))
    else:
        ocp.bound('r_n2b_n_z',(-2000, -0.5))
    ocp.bound('r',(100,100))

    for e in ['e11','e21','e31','e12','e22','e32','e13','e23','e33']:
        ocp.bound(e,(-1.1,1.1))

    for d in ['v_bn_n_x','v_bn_n_y','v_bn_n_z']:
        ocp.bound(d,(-70,70))

    for w in ['w_bn_b_x',
              'w_bn_b_y',
              'w_bn_b_z']:
        ocp.bound(w,(-4*pi,4*pi))

    ocp.bound('endTime',(4.0,4.0))
    ocp.guess('endTime',4.0)
    ocp.bound('w0',(10,10))

    # boundary conditions
    ocp.bound('r_n2b_n_y',(0,0),timestep=0,quiet=True)

    return ocp


if __name__=='__main__':
    from rawe.models.betty_conf import makeConf
    conf = makeConf()
    conf['runHomotopy'] = True
    conf['minAltitude'] = 0.5
    nk = 30
    dae = rawe.models.crosswind_drag(conf)
    dae.addP('endTime')

    print "setting up ocp..."
    ocp = setupOcp(dae,conf,nk)

    lineRadiusGuess = 100.0
    circleRadiusGuess = 20.0

    # trajectory for homotopy
    homotopyTraj = {'r_n2b_n_x':[],'r_n2b_n_y':[],'r_n2b_n_z':[]}
    k = 0
    for nkIdx in range(ocp.nk+1):
        for nicpIdx in range(ocp.nicp):
            if nkIdx == ocp.nk and nicpIdx > 0:
                break
            for degIdx in range(ocp.deg+1):
                if nkIdx == ocp.nk and degIdx > 0:
                    break

                r = circleRadiusGuess
                h = numpy.sqrt(lineRadiusGuess**2 - r**2)
                nTurns = 1

                # path following
                theta = 2*pi*(k+ocp.lagrangePoly.tau_root[degIdx])/float(ocp.nk*ocp.nicp)
                theta -= pi

                thetaDot = nTurns*2*pi/(ocp._guess.lookup('endTime'))
                xyzCircleFrame    = numpy.array([h, r*numpy.sin(theta),          -r*numpy.cos(theta)])
                xyzDotCircleFrame = numpy.array([0, r*numpy.cos(theta)*thetaDot,  r*numpy.sin(theta)*thetaDot])

                phi  = numpy.arcsin(r/lineRadiusGuess) # rotate so it's above ground
                phi += numpy.arcsin((conf['minAltitude']+0.3)/lineRadiusGuess)
                phi += 10*pi/180
                R_c2n = numpy.matrix([[  numpy.cos(phi), 0, numpy.sin(phi)],
                                      [               0, 1,              0],
                                      [ -numpy.sin(phi), 0, numpy.cos(phi)]])
                xyz    = numpy.dot(R_c2n, xyzCircleFrame)
                xyzDot = numpy.dot(R_c2n, xyzDotCircleFrame)

                if nicpIdx == 0 and degIdx == 0:
                    homotopyTraj['r_n2b_n_x'].append(float(xyz[0,0]))
                    homotopyTraj['r_n2b_n_y'].append(float(xyz[0,1]))
                    homotopyTraj['r_n2b_n_z'].append(float(xyz[0,2]))

                x = float(xyz[0,0])
                y = float(xyz[0,1])
                z = float(xyz[0,2])

                dx = float(xyzDot[0,0])
                dy = float(xyzDot[0,1])
                dz = float(xyzDot[0,2])

                ocp.guess('r_n2b_n_x',x,timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('r_n2b_n_y',y,timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('r_n2b_n_z',z,timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('v_bn_n_x',dx,timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('v_bn_n_y',dy,timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('v_bn_n_z',dz,timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)

                p0 = numpy.array([x,y,z])
                dp0 = numpy.array([dx,dy,dz])
                e1 = dp0/numpy.linalg.norm(dp0)
                e3 = -p0/lineRadiusGuess
                e2 = numpy.cross(e3,e1)

                ocp.guess('e11',e1[0],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('e12',e1[1],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('e13',e1[2],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)

                ocp.guess('e21',e2[0],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('e22',e2[1],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('e23',e2[2],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)

                ocp.guess('e31',e3[0],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('e32',e3[1],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)
                ocp.guess('e33',e3[2],timestep=nkIdx,nicpIdx=nicpIdx,degIdx=degIdx)

            k += 1
    ocp.guess('w_bn_b_z', -2.0*pi/ocp._guess.lookup('endTime'))

    # objective function
    obj = -1e6*ocp.lookup('gamma_homotopy')
    mean_x = numpy.mean(homotopyTraj['r_n2b_n_x'])
    mean_y = numpy.mean(homotopyTraj['r_n2b_n_y'])
    mean_z = numpy.mean(homotopyTraj['r_n2b_n_z'])
    for k in range(ocp.nk+1):
        x = ocp.lookup('r_n2b_n_x',timestep=k)
        y = ocp.lookup('r_n2b_n_y',timestep=k)
        z = ocp.lookup('r_n2b_n_z',timestep=k)
        obj += ((x-mean_x)**2 + (y-mean_y)**2 + (z-mean_z)**2 - circleRadiusGuess**2)**2 / float(ocp.nk)

#    for k in range(ocp.nk+1):
#        obj += (homotopyTraj['x'][k] - ocp.lookup('x',timestep=k))**2
#        obj += (homotopyTraj['y'][k] - ocp.lookup('y',timestep=k))**2
#        obj += (homotopyTraj['z'][k] - ocp.lookup('z',timestep=k))**2
    ocp.setQuadratureDdt('prop_energy', 'prop_power')

    # control regularization
    for k in range(ocp.nk):
        daileron = ocp.lookup('daileron',timestep=k)
        delevator = ocp.lookup('delevator',timestep=k)
        drudder = ocp.lookup('drudder',timestep=k)
        dflaps = ocp.lookup('dflaps',timestep=k)
        dprop_drag = ocp.lookup('dprop_drag',timestep=k)

        daileronSigma = 0.01
        delevatorSigma = 0.1
        drudderSigma = 0.1
        dflapsSigma = 0.1
        dpropSigma = 100.0

        ailObj = daileron*daileron / (daileronSigma*daileronSigma)
        eleObj = delevator*delevator / (delevatorSigma*delevatorSigma)
        rudObj = drudder*drudder / (drudderSigma*drudderSigma)
        flapsObj = dflaps*dflaps / (dflapsSigma*dflapsSigma)
        propObj = dprop_drag*dprop_drag / (dpropSigma*dpropSigma)

        obj += 1e-2*(ailObj + eleObj + rudObj + flapsObj + propObj)/float(ocp.nk)

    # homotopy forces/torques regularization
    homoReg = 0
    for k in range(ocp.nk):
        for nicpIdx in range(ocp.nicp):
            for degIdx in range(1,ocp.deg+1):
                homoReg += ocp.lookup('f1_homotopy',timestep=k,nicpIdx=nicpIdx,degIdx=degIdx)**2
                homoReg += ocp.lookup('f2_homotopy',timestep=k,nicpIdx=nicpIdx,degIdx=degIdx)**2
                homoReg += ocp.lookup('f3_homotopy',timestep=k,nicpIdx=nicpIdx,degIdx=degIdx)**2
                homoReg += ocp.lookup('t1_homotopy',timestep=k,nicpIdx=nicpIdx,degIdx=degIdx)**2
                homoReg += ocp.lookup('t2_homotopy',timestep=k,nicpIdx=nicpIdx,degIdx=degIdx)**2
                homoReg += ocp.lookup('t3_homotopy',timestep=k,nicpIdx=nicpIdx,degIdx=degIdx)**2
    obj += 1e-2*homoReg/float(ocp.nk*ocp.nicp*ocp.deg)

    ocp.setObjective( obj )

    # initial guesses
    ocp.guess('w0',10)
    ocp.guess('r',lineRadiusGuess)

    for name in ['w_bn_b_x','w_bn_b_y','aileron','rudder','flaps','prop_drag',
                 'elevator','daileron','delevator','drudder','dflaps','dprop_drag']:
        ocp.guess(name,0)

    ocp.guess('gamma_homotopy',0)

    # spawn telemetry thread
    callback = rawe.telemetry.startTelemetry(
        ocp, callbacks=[
            (rawe.telemetry.trajectoryCallback(toProto, Trajectory, showAllPoints=True), 'drag trajectory')
            ])#], printBoundViolation=True, printConstraintViolation=True)

    # solver
    solverOptions = [("linear_solver","ma27"),
                     ("max_iter",1000),
                     ("expand",True),
                     ("tol",1e-4)]

    print "setting up solver..."
    ocp.setupSolver( solverOpts=solverOptions,
                     callback=callback )

    xInit = None
    ocp.bound('gamma_homotopy',(1e-4,1e-4),force=True)
    traj = ocp.solve(xInit=xInit)

    ocp.bound('gamma_homotopy',(0,1),force=True)
    traj = ocp.solve(xInit=traj.getDvs())

    ocp.bound('gamma_homotopy',(1,1),force=True)
#    ocp.bound('endTime',(3.5,6.0),force=True)
    traj = ocp.solve(xInit=traj.getDvs())

    traj.save("data/crosswind_homotopy.dat")

    def printBoundsFeedback():
        # bounds feedback
        xOpt = traj.dvMap.vectorize()
        lbx = ocp.solver.input('lbx')
        ubx = ocp.solver.input('ubx')
        ocp._bounds.printBoundsFeedback(xOpt,lbx,ubx,reportThreshold=0)
    printBoundsFeedback()

    # Plot the results
    def plotResults():
        traj.subplot(['f1_homotopy','f2_homotopy','f3_homotopy'])
        traj.subplot(['t1_homotopy','t2_homotopy','t3_homotopy'])
        traj.subplot(['r_n2b_n_x','r_n2b_n_y','r_n2b_n_z'])
        traj.subplot(['v_bn_n_x','v_bn_n_y','v_bn_n_z'])
        traj.subplot([['aileron','elevator'],['daileron','delevator']],title='control surfaces')
        traj.subplot(['wind_at_altitude','v_bn_n_x'])
        traj.subplot(['c','cdot','cddot'],title="invariants")
        traj.plot('airspeed')
        traj.subplot([['alpha_deg'],['beta_deg']])
        traj.subplot(['cL','cD','L_over_D'])
        traj.subplot(['prop_power', 'prop_drag'])
        traj.subplot(['w_bn_b_x','w_bn_b_y','w_bn_b_z'])
        traj.subplot(['e11','e12','e13','e21','e22','e23','e31','e32','e33'])
        traj.plot(['nu'])
        plt.show()
    plotResults()
